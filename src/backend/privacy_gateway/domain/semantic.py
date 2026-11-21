import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from privacy_gateway.domain.redaction import PrivacyError

Category = Literal["personal", "health", "financial", "credential", "confidential"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: int = Field(ge=0)
    quote: str = Field(min_length=1)


class Proposal(Evidence):
    category: Category
    evidence: Evidence


class Proposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    findings: list[Proposal]


@dataclass(frozen=True)
class Protection:
    quote: str
    category: str
    source: int
    evidence_source: int
    evidence_quote: str

    @property
    def restore_allowed(self) -> bool:
        return self.category != "credential"


@dataclass(frozen=True)
class SemanticReport:
    protections: tuple[Protection, ...]
    model: str
    elapsed_ms: int
    context_messages: int


def ground_proposals(proposals: list[Proposal], sources: tuple[str, ...]) -> tuple[Protection, ...]:
    grounded = []
    for item in proposals:
        for evidence in (item, item.evidence):
            if evidence.message >= len(sources) or not evidence.quote.strip():
                raise PrivacyError("SEMANTIC_UNGROUNDED")
            if evidence.quote not in sources[evidence.message]:
                match = re.search(
                    r"\s*".join(map(re.escape, evidence.quote.split())),
                    sources[evidence.message],
                    re.IGNORECASE,
                )
                if match is None:
                    raise PrivacyError("SEMANTIC_UNGROUNDED")
                evidence.quote = match.group(0)
        grounded.append(
            Protection(
                item.quote,
                item.category,
                item.message,
                item.evidence.message,
                item.evidence.quote,
            )
        )
    return tuple(grounded)


def punctuation_span(quote: str, source: str) -> str | None:
    wanted = "".join(c.casefold() for c in quote if c.isalnum())
    if not wanted:
        return None
    positions, letters = [], []
    for index, char in enumerate(source):
        if char.isalnum():
            for letter in char.casefold():
                positions.append(index)
                letters.append(letter)
    normalized = "".join(letters)
    matches = []
    start = 0
    while (start := normalized.find(wanted, start)) >= 0:
        left, right = positions[start], positions[start + len(wanted) - 1] + 1
        if (left == 0 or not source[left - 1].isalnum()) and (
            right == len(source) or not source[right].isalnum()
        ):
            matches.append(source[left:right])
        start += 1
    return matches[0] if len(matches) == 1 else None
