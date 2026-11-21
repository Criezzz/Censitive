from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

from privacy_gateway.adapters import local_detector
from privacy_gateway.settings import CHECK_OPTIONS, PROSE_OPTIONS, LOCAL_ENDPOINT, MODEL
from privacy_gateway.domain import generalize
from privacy_gateway.domain.redaction import PrivacyError

PROMPTS = json.loads(Path(__file__).with_name("papillon_prompt.json").read_text("utf-8"))
FACT_INSTRUCTION = (
    "Do not add specific facts (numbers, prices, discounts, dates, job "
    "titles, achievements, credentials) that appear neither in the "
    "user's request nor in the model's response."
)
RESTORE_INSTRUCTION = (
    "Restore original values from these local bindings where the final task needs them."
)
_RESPONSE_DESCRIPTION = (
    "You have the following information from a better language model responding to "
    "related query or queries. Complete the user query by referencing this "
    "information. Only you have access to this information."
)


def method_messages(
    request: str, response: str | None = None, *, instruction: str | None = None
) -> list[dict[str, str]]:
    creator = response is None
    inputs = [("userQuery", "The user's request to be fulfilled.", request)]
    outputs = [("reasoning", " "), ("createdPrompt", "")] if creator else [("finalOutput", "")]
    if not creator:
        assert response is not None
        inputs.append(
            (
                "modelExampleResponses",
                _RESPONSE_DESCRIPTION,
                f"======\n\n{response.strip()}\n\n======",
            )
        )
    if instruction is None:
        instruction = (
            PROMPTS["prompt_creater"]["extended_signature_instructions"]
            if creator
            else PROMPTS["info_aggregator"]["signature_instructions"] + "\n" + FACT_INSTRUCTION
        )
    descriptions = "Your input fields are:\n" + "\n".join(
        f"{i}. `{name}` (str): {description}" for i, (name, description, _) in enumerate(inputs, 1)
    )
    descriptions += "\nYour output fields are:\n" + "\n".join(
        f"{i}. `{name}` (str):{description}" for i, (name, description) in enumerate(outputs, 1)
    )
    fields = [name for name, _, _ in inputs] + [name for name, _ in outputs]
    structure = "\n\n".join(f"[[ ## {name} ## ]]\n{{{name}}}" for name in fields)
    system = (
        descriptions + "\nAll interactions will be structured in the following way, "
        "with the appropriate values filled in.\n\n"
        + structure
        + "\n\n[[ ## completed ## ]]\nIn adhering to this structure, your objective is: \n        "
        + instruction.replace("\n", "\n        ")
    )
    user = "\n\n".join(f"[[ ## {name} ## ]]\n{value}" for name, _, value in inputs)
    user += "\n\nRespond with the corresponding output fields, starting with the field "
    user += ", then ".join(f"`[[ ## {name} ## ]]`" for name, _ in outputs)
    user += ", and then ending with the marker for `[[ ## completed ## ]]`."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def method_field(raw: str, field: str, *, creator: bool = False) -> str:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^\s*\[\[ ## (\w+) ## \]\]\s*", raw))
    for index, match in enumerate(matches):
        sections.setdefault(
            match[1],
            raw[
                match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            ].strip(),
        )
    if field not in sections or (creator and "reasoning" not in sections) or not sections[field]:
        raise PrivacyError("METHOD_INVALID_OUTPUT")
    return sections[field]


class LocalCensitiveMethod:
    def __init__(self, *, key: str = "") -> None:
        self.model = MODEL
        self.key = key
        self.lock = asyncio.Lock()
        self.detector = local_detector.LocalDetector(self.key, self.lock)

    async def preflight(self) -> None:
        if not self.key:
            raise PrivacyError("METHOD_UNAVAILABLE")
        try:
            async with httpx.AsyncClient(
                base_url=LOCAL_ENDPOINT,
                trust_env=False,
                follow_redirects=False,
                timeout=10,
                headers={"Authorization": f"Bearer {self.key}"},
            ) as client:
                response = await client.get("/v1/models")
                if response.status_code != 200 or self.model not in {
                    item.get("id")
                    for item in response.json().get("data", [])
                    if isinstance(item, dict)
                }:
                    raise PrivacyError("METHOD_UNAVAILABLE")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise PrivacyError("METHOD_UNAVAILABLE") from None

    async def _complete(self, payload: dict[str, Any]) -> str:
        if not self.key:
            raise PrivacyError("METHOD_UNAVAILABLE")
        async with self.lock:
            return await local_detector.complete_local_json(payload, model=self.model, key=self.key)

    async def _prose(self, messages: list[dict[str, str]], field: str) -> str:
        raw = await self._complete(
            {
                "model": self.model,
                "messages": messages,
                **PROSE_OPTIONS,
            }
        )
        return method_field(raw, field, creator=field == "createdPrompt")

    async def create(self, request: str) -> str:
        return await self._prose(method_messages(request), "createdPrompt")

    async def check(self, request: str, created: str, candidates: list[str]) -> str:
        instruction, schema = generalize.leak_contract()
        return await self._complete(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instruction},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "original_request": request,
                                "rewritten_prompt": created,
                                "candidates": candidates,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                **CHECK_OPTIONS,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "v3_1p7b_leak_check",
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
        )

    async def aggregate(
        self, request: str, response: str, mapping: dict[str, str], note: str = ""
    ) -> str:
        instruction = PROMPTS["info_aggregator"]["signature_instructions"] + "\n" + FACT_INSTRUCTION
        if mapping:
            instruction += (
                "\n" + RESTORE_INSTRUCTION + "\n" + json.dumps(mapping, ensure_ascii=False)
            )
        if note:
            instruction += "\n" + note
        answer = await self._prose(
            method_messages(request, response, instruction=instruction), "finalOutput"
        )
        return answer.strip().removesuffix("======").rstrip()
