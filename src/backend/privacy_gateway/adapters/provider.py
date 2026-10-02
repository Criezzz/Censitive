import json
from collections.abc import AsyncGenerator, Mapping
from typing import Protocol, runtime_checkable

from privacy_gateway.domain.redaction import PrivacyError


class Provider(Protocol):
    provider_id: str
    model: str
    serializer_version: str
    timeout: float

    async def complete(self, body: bytes) -> str: ...


@runtime_checkable
class StreamingProvider(Protocol):
    @property
    def request_options(self) -> Mapping[str, object]: ...

    def stream(self, body: bytes) -> AsyncGenerator[str, None]: ...


def serialize_request(provider: Provider, messages: list[dict[str, str]]) -> bytes:
    options = dict(provider.request_options) if isinstance(provider, StreamingProvider) else {}
    if options.keys() & {"model", "messages"}:
        raise PrivacyError("INVALID_PAYLOAD")
    try:
        return json.dumps(
            {"model": provider.model, "messages": messages, **options},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise PrivacyError("INVALID_PAYLOAD") from None
