import json
import os
import re

import httpx

from privacy_gateway.domain.redaction import PrivacyError

DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"
NOT_CHAT = re.compile(
    r"embedding|tts|whisper|dall-e|image|audio|realtime|transcribe|moderation|search|instruct|codex|computer|davinci|babbage|live|-pro|deep-research|\d{4}-\d{2}-\d{2}$"
)


class OpenAIProvider:
    provider_id = "openai-api"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        key: str = "",
    ) -> None:
        self.model = model
        self.key = key

    @classmethod
    def from_environment(cls) -> "OpenAIProvider":
        return cls(key=os.environ.get("OPENAI_API_KEY", ""))

    def with_model(self, model: str) -> "OpenAIProvider":
        return OpenAIProvider(model, key=self.key)

    def configuration_error(self) -> str:
        if not self.key:
            return "OPENAI_API_KEY_REQUIRED"
        if any(not 33 <= ord(c) <= 126 for c in self.key):
            return "OPENAI_API_KEY_INVALID"
        return ""

    def body(self, text: str) -> bytes:
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": text}],
            "store": True,
            "metadata": {"app": "censitive", "contract": "prose"},
        }
        if re.match(r"(gpt-4|gpt-3\.5|chatgpt-4o)", self.model):
            payload["temperature"] = 0
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=20
            ) as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {self.key}"},
                )
                response.raise_for_status()
            found = [
                (item.get("created") or 0, item["id"])
                for item in response.json()["data"]
                if isinstance(item.get("id"), str)
                and re.match(r"(gpt-|o\d|chatgpt-)", item["id"])
                and not NOT_CHAT.search(item["id"])
            ]
            return [model for _, model in sorted(found, reverse=True)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise PrivacyError("PROVIDER_FAILED") from None

    async def complete(self, body: bytes) -> str:
        code = self.configuration_error()
        if code:
            raise PrivacyError(code)
        try:
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=120
            ) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {self.key}",
                        "Content-Type": "application/json",
                    },
                )
            code = {
                401: "OPENAI_AUTH_FAILED",
                403: "OPENAI_AUTH_FAILED",
                429: "OPENAI_RATE_LIMIT",
                400: "OPENAI_REQUEST_REJECTED",
                404: "OPENAI_REQUEST_REJECTED",
                422: "OPENAI_REQUEST_REJECTED",
            }.get(response.status_code)
            if code:
                raise PrivacyError(code)
            if response.status_code != 200:
                raise PrivacyError("PROVIDER_FAILED")
            result = response.json()
            if result.get("status") == "incomplete":
                raise PrivacyError("OPENAI_INCOMPLETE")
            if result.get("status") != "completed":
                raise PrivacyError("PROVIDER_FAILED")
            parts = []
            for item in result["output"]:
                if item.get("type") == "reasoning":
                    continue
                if item.get("type") != "message" or item.get("role") != "assistant":
                    raise PrivacyError("PROVIDER_FAILED")
                for part in item["content"]:
                    if part.get("type") == "refusal":
                        raise PrivacyError("OPENAI_REFUSED")
                    if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                        raise PrivacyError("PROVIDER_FAILED")
                    parts.append(part["text"])
            answer = "".join(parts)
            if not answer.strip():
                raise PrivacyError("PROVIDER_FAILED")
            return answer
        except httpx.TimeoutException:
            raise PrivacyError("PROVIDER_TIMEOUT") from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, PrivacyError):
                raise
            raise PrivacyError("PROVIDER_FAILED") from None
