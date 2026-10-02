import asyncio
import json
import os
from collections.abc import AsyncGenerator, Mapping

import httpx

from privacy_gateway.adapters.local_detector import ENDPOINT
from privacy_gateway.adapters.model_profiles import model_profile
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json
from privacy_gateway.domain.streaming import SSEDecoder


class LocalChatProvider:
    provider_id = "local-qwen"
    serializer_version = "llama-sse-v1"
    timeout = 180.0

    def __init__(self, profile: str = "8b", *, key: str | None = None, lock: asyncio.Lock) -> None:
        self.model = model_profile(profile).model_id
        self.key = os.environ.get("CENSITIVE_LOCAL_KEY", "") if key is None else key
        self.lock = lock

    @property
    def request_options(self) -> Mapping[str, object]:
        return {
            "stream": True,
            "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0,
            "seed": 17,
            "max_tokens": 512,
        }

    async def complete(self, body: bytes) -> str:
        pieces = []
        async for delta in self.stream(body):
            pieces.append(delta)
        return "".join(pieces)

    async def stream(self, body: bytes) -> AsyncGenerator[str, None]:
        if not self.key:
            raise PrivacyError("LOCAL_CHAT_UNAVAILABLE")
        try:
            payload = strict_json(body)
            if (
                not isinstance(payload, dict)
                or payload.get("model") != self.model
                or set(payload) != {"model", "messages", *self.request_options}
                or any(
                    json.dumps(payload.get(k), sort_keys=True) != json.dumps(v, sort_keys=True)
                    for k, v in self.request_options.items()
                )
                or not isinstance(payload["messages"], list)
                or not 1 <= len(payload["messages"]) <= 40
                or any(
                    not isinstance(m, dict)
                    or set(m) != {"role", "content"}
                    or m["role"] not in {"user", "assistant"}
                    or not isinstance(m["content"], str)
                    for m in payload["messages"]
                )
            ):
                raise PrivacyError("INVALID_PAYLOAD")
            if sum(len(m["content"].encode("utf-8")) for m in payload["messages"]) > 6000:
                raise PrivacyError("LOCAL_CHAT_CONTEXT_LIMIT")
        except (ValueError, TypeError, KeyError) as error:
            if isinstance(error, PrivacyError):
                raise
            raise PrivacyError("INVALID_PAYLOAD") from None
        if self.lock.locked():
            raise PrivacyError("SEMANTIC_BUSY")
        async with self.lock:
            decoder = SSEDecoder()
            stopped = done = False
            identity: str | None = None
            received = emitted = frames = 0
            try:
                async with (
                    asyncio.timeout(self.timeout),
                    httpx.AsyncClient(
                        trust_env=False,
                        follow_redirects=False,
                        timeout=self.timeout,
                        headers={"Authorization": f"Bearer {self.key}"},
                    ) as client,
                ):
                    async with client.stream(
                        "POST",
                        ENDPOINT + "/v1/chat/completions",
                        content=body,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("content-type", "").split(";", 1)[0]
                            != "text/event-stream"
                        ):
                            raise PrivacyError("LOCAL_CHAT_UNAVAILABLE")
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received > 800_000:
                                raise PrivacyError("RESPONSE_LIMIT")
                            for event in decoder.feed(chunk):
                                frames += 1
                                if frames > 10_000 or done or event.event not in {"", "message"}:
                                    raise PrivacyError("STREAM_INVALID_OUTPUT")
                                if event.data == "[DONE]":
                                    if not stopped:
                                        raise PrivacyError("STREAM_INCOMPLETE")
                                    done = True
                                    continue
                                item = strict_json(event.data, code="STREAM_INVALID_OUTPUT")
                                if (
                                    not isinstance(item, dict)
                                    or item.get("model") != self.model
                                    or not isinstance(item.get("id"), str)
                                    or not item["id"]
                                    or (identity is not None and item["id"] != identity)
                                    or stopped
                                    or not isinstance(item.get("choices"), list)
                                    or len(item["choices"]) != 1
                                ):
                                    raise PrivacyError("STREAM_INVALID_OUTPUT")
                                identity = item["id"]
                                choice = item["choices"][0]
                                delta = choice["delta"]
                                if (
                                    type(choice.get("index")) is not int
                                    or choice["index"] != 0
                                    or not isinstance(delta, dict)
                                    or set(delta) - {"role", "content", "reasoning_content"}
                                    or delta.get("role", "assistant") != "assistant"
                                    or delta.get("reasoning_content") not in {None, ""}
                                    or choice.get("finish_reason") not in {None, "stop"}
                                ):
                                    raise PrivacyError("STREAM_INVALID_OUTPUT")
                                content = delta.get("content")
                                if content is not None:
                                    if not isinstance(content, str):
                                        raise PrivacyError("STREAM_INVALID_OUTPUT")
                                    emitted += len(content.encode("utf-8"))
                                    if emitted > 200_000:
                                        raise PrivacyError("RESPONSE_LIMIT")
                                    if content:
                                        yield content
                                stopped = choice.get("finish_reason") == "stop"
                        decoder.finish()
                        if not done or not stopped:
                            raise PrivacyError("STREAM_INCOMPLETE")
            except (httpx.TimeoutException, TimeoutError):
                raise PrivacyError("LOCAL_CHAT_TIMEOUT") from None
            except httpx.HTTPError:
                raise PrivacyError("LOCAL_CHAT_UNAVAILABLE") from None
            except (ValueError, TypeError, KeyError, IndexError, UnicodeError) as error:
                if isinstance(error, PrivacyError):
                    raise
                raise PrivacyError("STREAM_INVALID_OUTPUT") from None
