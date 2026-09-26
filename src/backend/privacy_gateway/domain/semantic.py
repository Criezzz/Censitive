from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from privacy_gateway.domain.redaction import PREFIX, PrivacyError

Category = Literal["personal", "health", "financial", "credential", "confidential"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=800)


class Proposal(Evidence):
    category: Category
    evidence: Evidence


class Proposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    findings: list[Proposal] = Field(max_length=32)


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


def ground_proposals(raw: str, sources: tuple[str, ...]) -> tuple[Protection, ...]:
    try:
        proposals = Proposals.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    grounded = []
    for item in proposals.findings:
        for evidence in (item, item.evidence):
            if (
                evidence.message >= len(sources)
                or not evidence.quote.strip()
                or PREFIX in evidence.quote
                or evidence.quote not in sources[evidence.message]
            ):
                raise PrivacyError("SEMANTIC_UNGROUNDED")
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
