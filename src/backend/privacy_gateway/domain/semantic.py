import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from privacy_gateway.domain.presend import literal_pattern
from privacy_gateway.domain.redaction import PREFIX, PrivacyError
from privacy_gateway.domain.serialization import strict_json

Category = Literal["personal", "health", "financial", "credential", "confidential"]
MAX_FINDINGS = 32


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=800)


class Proposal(Evidence):
    category: Category
    evidence: Evidence


class Proposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    findings: list[Proposal] = Field(max_length=MAX_FINDINGS)


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


def ground_proposals(
    raw: str, sources: tuple[str, ...], *, strict_edges: bool = False
) -> tuple[Protection, ...]:
    try:
        proposals = Proposals.model_validate(strict_json(raw, code="SEMANTIC_INVALID_OUTPUT"))
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    grounded = []
    for item in proposals.findings:
        for evidence in (item, item.evidence):
            if (
                evidence.message >= len(sources)
                or not evidence.quote.strip()
                or PREFIX in evidence.quote
            ):
                raise PrivacyError("SEMANTIC_UNGROUNDED")
            if strict_edges:
                match = re.search(literal_pattern(evidence.quote), sources[evidence.message], re.I)
                if match is None or PREFIX in match.group():
                    raise PrivacyError("SEMANTIC_UNGROUNDED")
                evidence.quote = match.group()
            elif evidence.quote not in sources[evidence.message]:
                match = re.search(
                    r"\s*".join(map(re.escape, evidence.quote.split())),
                    sources[evidence.message],
                    re.IGNORECASE,
                )
                if match is None or PREFIX in match.group(0):
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
    return matches[0] if len(matches) == 1 and PREFIX not in matches[0] else None
