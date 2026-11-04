import asyncio
import secrets
import time
from dataclasses import dataclass, field

import httpx

from privacy_gateway.domain.jev import (
    JEV_ENDPOINT,
    JEV_MODEL,
    JEV_VERSION,
    JevAssessment,
    parse_jev_response,
)
from privacy_gateway.domain.redaction import PrivacyError


@dataclass
class JevGate:
    key: str = field(repr=False)
    generation: str = field(default_factory=lambda: secrets.token_hex(16))
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not self.key
            or len(self.key) > 4096
            or not self.key.isascii()
            or any(c.isspace() for c in self.key)
        ):
            raise PrivacyError("JEV_KEY_INVALID")

    @property
    def signature(self) -> tuple[str, str, str]:
        return ("typesafe-jev", JEV_MODEL, JEV_VERSION + ":" + self.generation)

    def revoke(self) -> None:
        self.key = ""
        self.generation = secrets.token_hex(16)

    def require_key(self) -> None:
        if not self.key:
            raise PrivacyError("JEV_KEY_REQUIRED")

    async def assess(self, body: bytes) -> JevAssessment:
        self.require_key()
        started = time.monotonic()
        try:
            async with (
                asyncio.timeout(30),
                httpx.AsyncClient(
                    timeout=10,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    JEV_ENDPOINT,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {self.key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise PrivacyError("JEV_HTTP_ERROR")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 32768:
                            raise PrivacyError("JEV_RESPONSE_INVALID")
        except (TimeoutError, httpx.TimeoutException):
            raise PrivacyError("JEV_TIMEOUT") from None
        except httpx.HTTPError:
            raise PrivacyError("JEV_CONNECTION_FAILED") from None
        return parse_jev_response(bytes(raw), body, round((time.monotonic() - started) * 1000))
