from __future__ import annotations

import httpx

from privacy_gateway.domain.limits import MAX_REQUEST_BYTES
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json

_CONTEXT_SIZE = 8192
_PROPS_PATH = "/props"
_INPUT_TOKENS_PATH = "/v1/chat/completions/input_tokens"
_PROPS_RESPONSE_LIMIT = 128 * 1024
_INPUT_TOKENS_RESPONSE_LIMIT = 4 * 1024
_UNAVAILABLE = "LOCAL_CONTEXT_UNAVAILABLE"


def _validate_body(body: bytes, *, limit_code: str) -> int:
    if type(body) is not bytes:
        raise PrivacyError(_UNAVAILABLE)
    if len(body) > MAX_REQUEST_BYTES:
        raise PrivacyError(limit_code)
    payload = strict_json(body, code=_UNAVAILABLE)
    if type(payload) is not dict:
        raise PrivacyError(_UNAVAILABLE)
    max_tokens = payload.get("max_tokens")
    if type(max_tokens) is not int or max_tokens <= 0 or max_tokens > _CONTEXT_SIZE:
        raise PrivacyError(_UNAVAILABLE)
    return max_tokens


async def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            raise PrivacyError(_UNAVAILABLE) from None
        if declared < 0 or declared > limit:
            raise PrivacyError(_UNAVAILABLE)

    data = bytearray()
    async for chunk in response.aiter_bytes():
        if len(data) + len(chunk) > limit:
            raise PrivacyError(_UNAVAILABLE)
        data.extend(chunk)
    return bytes(data)


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    limit: int,
    content: bytes | None = None,
) -> object:
    try:
        headers = {"Content-Type": "application/json"} if content is not None else None
        async with client.stream(method, path, content=content, headers=headers) as response:
            if response.status_code != 200:
                raise PrivacyError(_UNAVAILABLE)
            raw = await _read_bounded(response, limit)
        return strict_json(raw, code=_UNAVAILABLE)
    except PrivacyError:
        raise
    except Exception:
        raise PrivacyError(_UNAVAILABLE) from None


def _validate_props(props: object) -> None:
    if type(props) is not dict:
        raise PrivacyError(_UNAVAILABLE)
    settings = props.get("default_generation_settings")
    if type(settings) is not dict:
        raise PrivacyError(_UNAVAILABLE)
    if type(settings.get("n_ctx")) is not int or settings["n_ctx"] != _CONTEXT_SIZE:
        raise PrivacyError(_UNAVAILABLE)
    if type(props.get("total_slots")) is not int or props["total_slots"] != 1:
        raise PrivacyError(_UNAVAILABLE)


def _read_input_tokens(result: object) -> int:
    if type(result) is not dict or set(result) != {"object", "input_tokens"}:
        raise PrivacyError(_UNAVAILABLE)
    if result.get("object") != "response.input_tokens":
        raise PrivacyError(_UNAVAILABLE)
    input_tokens = result.get("input_tokens")
    if type(input_tokens) is not int or input_tokens <= 0:
        raise PrivacyError(_UNAVAILABLE)
    return input_tokens


async def check_local_context(client: httpx.AsyncClient, body: bytes, *, limit_code: str) -> None:
    max_tokens = _validate_body(body, limit_code=limit_code)
    props = await _request_json(
        client,
        "GET",
        _PROPS_PATH,
        limit=_PROPS_RESPONSE_LIMIT,
    )
    _validate_props(props)
    result = await _request_json(
        client,
        "POST",
        _INPUT_TOKENS_PATH,
        limit=_INPUT_TOKENS_RESPONSE_LIMIT,
        content=body,
    )
    input_tokens = _read_input_tokens(result)
    if input_tokens + max_tokens + 1 > _CONTEXT_SIZE:
        raise PrivacyError(limit_code)
