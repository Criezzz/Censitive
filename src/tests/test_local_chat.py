import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest
from privacy_gateway.adapters.local_chat import LocalChatProvider
from privacy_gateway.adapters.provider import serialize_request
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError


def frame(content: object = "hello", *, finish: object = None, **overrides: object) -> bytes:
    item = {
        "id": "synthetic-1",
        "model": "qwen3-8b-local",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}],
        **overrides,
    }
    return ("data: " + json.dumps(item) + "\n\n").encode()


@pytest.mark.parametrize(
    "kind",
    [
        "valid",
        "missing_done",
        "done_first",
        "wrong_model",
        "changed_id",
        "length",
        "tool",
        "post_done",
        "http",
        "utf8",
        "duplicate_identity",
        "duplicate_content",
        "nonfinite",
    ],
)
def test_local_stream_transport_exact_bytes_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    provider = LocalChatProvider(key="synthetic-key", lock=asyncio.Lock())
    body = serialize_request(provider, [{"role": "user", "content": "public synthetic"}])
    valid = frame("Xin chào 🙂") + frame(None, finish="stop") + b"data: [DONE]\n\n"
    samples = {
        "valid": valid,
        "missing_done": frame("hello") + frame(None, finish="stop"),
        "done_first": b"data: [DONE]\n\n",
        "wrong_model": frame(model="wrong"),
        "changed_id": frame() + frame(id="other"),
        "length": frame(finish="length"),
        "tool": frame(choices=[{"index": 0, "delta": {"tool_calls": []}, "finish_reason": None}]),
        "post_done": valid + frame(),
        "http": b"private-error",
        "utf8": b"data: \xff\n\n",
        "duplicate_identity": frame().replace(
            b'"id": "synthetic-1"', b'"id":"other","id":"synthetic-1"'
        ),
        "duplicate_content": frame().replace(
            b'"content": "hello"', b'"content":"hidden","content":"hello"'
        ),
        "nonfinite": frame().replace(b'"content": "hello"', b'"content":NaN'),
    }
    calls = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for value in samples[kind]:
                yield bytes([value])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        assert request.content == body
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert str(request.url) == "http://127.0.0.1:8011/v1/chat/completions"
        return httpx.Response(
            503 if kind == "http" else 200,
            stream=Chunks(),
            headers={"content-type": "text/event-stream"},
        )

    original = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client)
    if kind == "valid":
        assert asyncio.run(provider.complete(body)) == "Xin chào 🙂"
    else:
        with pytest.raises(PrivacyError):
            asyncio.run(provider.complete(body))
    assert calls == [body] and not provider.lock.locked()


def test_preview_binds_stream_settings_and_gate_rejects_modified_options() -> None:
    provider = LocalChatProvider(key="synthetic-key", lock=asyncio.Lock())
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "public", ())
    assert gateway.gate(prepared, session)[0].content == "public"
    payload = json.loads(prepared.body)
    assert payload["stream"] is True and payload["cache_prompt"] is False
    for changed in (False, 1):
        payload["stream"] = changed
        with pytest.raises(PrivacyError, match="INVALID_PAYLOAD"):
            gateway.gate(replace(prepared, body=json.dumps(payload).encode()), session)


def test_shared_slot_and_context_guard_never_send() -> None:
    async def run() -> None:
        lock = asyncio.Lock()
        provider = LocalChatProvider(key="synthetic", lock=lock)
        body = serialize_request(provider, [{"role": "user", "content": "public"}])
        async with lock:
            with pytest.raises(PrivacyError, match="SEMANTIC_BUSY"):
                await provider.complete(body)
        large = serialize_request(provider, [{"role": "user", "content": "🙂" * 1501}])
        with pytest.raises(PrivacyError, match="LOCAL_CHAT_CONTEXT_LIMIT"):
            await provider.complete(large)

    asyncio.run(run())
