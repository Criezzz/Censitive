from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from privacy_gateway.domain.identity import (
    Digest,
    DraftIdentity,
    DraftOccurrence,
    Occurrence,
    _committed_span,
    _span,
    apply_draft_identity,
)
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.semantic import Category
from privacy_gateway.domain.state import Fact, Source, State


class StrictFactCommand(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class CreateFact(StrictFactCommand):
    kind: Literal["create_fact"] = "create_fact"
    expected_epoch: int = Field(ge=0)
    evidence: Occurrence
    category: Category


class DraftFactMark(StrictFactCommand):
    occurrence: DraftOccurrence
    category: Category


class DraftFacts(StrictFactCommand):
    expected_epoch: int = Field(ge=0)
    text_digest: Digest
    marks: tuple[DraftFactMark, ...] = Field(min_length=1, max_length=16)

    @field_validator("marks", mode="before")
    @classmethod
    def freeze_marks(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


@dataclass(frozen=True)
class FactResult:
    state: State
    fact: Fact


def validate_manual_fact(command: CreateFact) -> CreateFact:
    if type(command) is not CreateFact:
        raise PrivacyError("STATE_INVALID")
    try:
        return CreateFact.model_validate(command)
    except ValidationError:
        raise PrivacyError("STATE_INVALID") from None


def apply_manual_fact(base: State, command: CreateFact) -> FactResult:
    base.validate()
    command = validate_manual_fact(command)
    if command.expected_epoch != base.epoch:
        raise PrivacyError("STATE_STALE")
    span = _committed_span(base, command.evidence)
    state, fact = base.add_fact(span, command.category, scope="fact")
    state.validate()
    return FactResult(state, fact)


def _source_only(base: State, current: Source, draft: DraftFacts) -> tuple[State, DraftFacts]:
    base.validate()
    if type(draft) is not DraftFacts or type(current) is not Source:
        raise PrivacyError("STATE_INVALID")
    if type(getattr(draft, "marks", None)) is not tuple:
        raise PrivacyError("STATE_INVALID")
    try:
        draft = DraftFacts.model_validate(draft)
    except ValidationError:
        raise PrivacyError("STATE_INVALID") from None
    if (
        draft.expected_epoch != base.epoch
        or draft.text_digest != current.digest
        or current.id in {source.id for source in base.sources}
    ):
        raise PrivacyError("STATE_STALE")
    state = replace(base, sources=(*base.sources, current), epoch=base.epoch + 1)
    state.validate()
    return state, draft


def apply_draft_facts(
    base: State,
    staged: State,
    current: Source,
    draft: DraftFacts,
    *,
    draft_identity: DraftIdentity | None = None,
) -> State:
    source_only, draft = _source_only(base, current, draft)
    if staged != source_only:
        raise PrivacyError("STATE_STALE")
    state = (
        apply_draft_identity(base, staged, current, draft_identity)
        if draft_identity is not None
        else staged
    )
    for mark in draft.marks:
        span = _span(current, mark.occurrence)
        state, _ = state.add_fact(span, mark.category, scope="fact")
    state.validate()
    return state


def verify_draft_facts(base: State, staged: State, current: Source, draft: DraftFacts) -> None:
    _, draft = _source_only(base, current, draft)
    staged.validate()
    if staged.source(current.id) != current:
        raise PrivacyError("STATE_SOURCE_MISMATCH")
    for mark in draft.marks:
        span = _span(current, mark.occurrence)
        if not any(
            fact not in base.facts
            and fact.assertion == span
            and fact.category == mark.category
            and fact.scope == "fact"
            and fact.entity_id is None
            and not fact.superseded
            for fact in staged.facts
        ):
            raise PrivacyError("STATE_FACT_NOT_APPLIED")
