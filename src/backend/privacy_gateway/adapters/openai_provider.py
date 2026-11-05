import asyncio
import json
import os
import re
from collections.abc import Mapping
from typing import Literal

import httpx

from privacy_gateway.domain.formula_plan import chat_instruction, chat_output_schema
from privacy_gateway.domain.limits import MAX_HISTORY_MESSAGES, MAX_REQUEST_BYTES
from privacy_gateway.domain.private_plan import planner_output_schema
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z")
DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"


class OpenAIProvider:
    provider_id = "openai-api"
    serializer_version = "openai-chat-v1"
    timeout = 120.0

    def __init__(
        self,
        model: str = "",
        *,
        key: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        contract: Literal["plain", "chat", "private"] = "plain",
    ) -> None:
        self.model = model
        self._key = key
        self._transport = transport
        self._busy = False
        self.contract = contract
        if contract not in {"plain", "chat", "private"}:
            raise ValueError("OPENAI_CONTRACT_INVALID")
        self.serializer_version = (
            "openai-chat-v1" if contract == "plain" else f"openai-{contract}-formula-v1"
        )

    @classmethod
    def from_environment(cls) -> "OpenAIProvider":
        return cls(
            os.environ.get("CENSITIVE_OPENAI_MODEL", DEFAULT_MODEL),
            key=os.environ.get("OPENAI_API_KEY", ""),
            contract="chat",
        )

    def private_planner(self) -> "OpenAIProvider":
        return OpenAIProvider(
            self.model, key=self._key, transport=self._transport, contract="private"
        )

    @property
    def qwen_formulas(self) -> bool:
        return self.contract == "private"

    def request_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        if self.contract != "chat":
            return messages
        return [{"role": "system", "content": chat_instruction(messages)}, *messages]

    def configuration_error(self) -> str:
        if not self._key:
            return "OPENAI_API_KEY_REQUIRED"
        if len(self._key) > 1024 or any(not 33 <= ord(c) <= 126 for c in self._key):
            return "OPENAI_API_KEY_INVALID"
        if not MODEL_ID.fullmatch(self.model):
            return "OPENAI_MODEL_REQUIRED"
        return ""

    @property
    def request_options(self) -> Mapping[str, object]:
        options: dict[str, object] = {
            "store": False,
            "max_completion_tokens": 512 if self.contract == "plain" else 2048,
        }
        if self.contract != "plain":
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "masked_chat_formula" if self.contract == "chat" else "public_plan",
                    "strict": True,
                    "schema": chat_output_schema()
                    if self.contract == "chat"
                    else planner_output_schema(),
                },
            }
        return options

    def validate_body(self, body: bytes) -> None:
        if len(body) > MAX_REQUEST_BYTES:
            raise PrivacyError("INVALID_PAYLOAD")
        payload = strict_json(body)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"model", "messages", *self.request_options}
            or payload["model"] != self.model
            or any(
                payload.get(k) != v or type(payload.get(k)) is not type(v)
                for k, v in self.request_options.items()
            )
            or not isinstance(payload["messages"], list)
            or not 1 <= len(payload["messages"]) <= MAX_HISTORY_MESSAGES + (self.contract == "chat")
            or any(
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not isinstance(message["role"], str)
                or message["role"]
                not in {"user", "assistant", *({"system"} if self.contract == "chat" else set())}
                or not isinstance(message["content"], str)
                for message in payload["messages"]
            )
        ):
            raise PrivacyError("INVALID_PAYLOAD")
        if json.dumps({k: payload[k] for k in self.request_options}, sort_keys=True) != json.dumps(
            dict(self.request_options), sort_keys=True
        ):
            raise PrivacyError("INVALID_PAYLOAD")
        if self.contract == "chat":
            messages = payload["messages"]
            if (
                len(messages) < 2
                or messages[0]["role"] != "system"
                or any(m["role"] == "system" for m in messages[1:])
                or messages != self.request_messages(messages[1:])
            ):
                raise PrivacyError("INVALID_PAYLOAD")

    async def complete(self, body: bytes) -> str:
        code = self.configuration_error()
        if code:
            raise PrivacyError(code)
        self.validate_body(body)
        if self._busy:
            raise PrivacyError("PROVIDER_BUSY")
        self._busy = True
        try:
            async with (
                asyncio.timeout(self.timeout),
                httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self.timeout,
                    transport=self._transport,
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code in {401, 403}:
                        raise PrivacyError("OPENAI_AUTH_FAILED")
                    if response.status_code == 429:
                        raise PrivacyError("OPENAI_RATE_LIMIT")
                    if response.status_code in {400, 404, 422}:
                        raise PrivacyError("OPENAI_REQUEST_REJECTED")
                    if response.status_code != 200:
                        raise PrivacyError("PROVIDER_FAILED")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64_000):
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise PrivacyError("RESPONSE_LIMIT")
            result = strict_json(bytes(raw), code="PROVIDER_FAILED")
            if not isinstance(result, dict) or result.get("object") != "chat.completion":
                raise PrivacyError("PROVIDER_FAILED")
            if set(result) - {
                "id",
                "object",
                "created",
                "model",
                "choices",
                "usage",
                "service_tier",
                "system_fingerprint",
            }:
                raise PrivacyError("PROVIDER_FAILED")
            if not isinstance(result.get("model"), str) or not MODEL_ID.fullmatch(result["model"]):
                raise PrivacyError("PROVIDER_FAILED")
            choices = result.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise PrivacyError("PROVIDER_FAILED")
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                raise PrivacyError("OPENAI_INCOMPLETE")
            if set(choice) - {"index", "message", "finish_reason", "logprobs"}:
                raise PrivacyError("PROVIDER_FAILED")
            if type(choice.get("index")) is not int or choice["index"] != 0:
                raise PrivacyError("PROVIDER_FAILED")
            message = choice.get("message")
            if isinstance(message, dict) and message.get("refusal") is not None:
                if self.contract != "plain":
                    raise PrivacyError("OPENAI_REFUSED")
            if (
                not isinstance(message, dict)
                or bool(
                    set(message)
                    - {
                        "role",
                        "content",
                        "refusal",
                        "tool_calls",
                        "function_call",
                        "audio",
                        "annotations",
                    }
                )
                or message.get("role") != "assistant"
                or message.get("tool_calls") not in (None, [])
                or message.get("function_call") is not None
                or message.get("audio") is not None
                or message.get("annotations") not in (None, [])
                or message.get("refusal") is not None
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
            ):
                raise PrivacyError("PROVIDER_FAILED")
            answer: str = message["content"]
            if len(answer.encode("utf-8")) > 200_000:
                raise PrivacyError("RESPONSE_LIMIT")
            return answer
        except (httpx.TimeoutException, TimeoutError):
            raise PrivacyError("PROVIDER_TIMEOUT") from None
        except httpx.HTTPError:
            raise PrivacyError("PROVIDER_FAILED") from None
        finally:
            self._busy = False
