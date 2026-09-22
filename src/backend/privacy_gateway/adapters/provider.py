"""The only application-to-provider boundary: approved bytes in, plain text out."""

from typing import Protocol


class Provider(Protocol):
    provider_id: str
    model: str
    serializer_version: str
    timeout: float

    async def complete(self, body: bytes) -> str: ...
