from dataclasses import dataclass, replace
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from privacy_gateway.domain.models import SourceSpan
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.state import Entity, Link, Source, State

IdentityID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DraftOccurrence(StrictIdentity):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=800)


class Occurrence(DraftOccurrence):
    source_id: IdentityID
    source_revision: int = Field(ge=1)
    source_digest: Digest


class CreateEntity(StrictIdentity):
    kind: Literal["create"] = "create"
    expected_epoch: int = Field(ge=0)
    evidence: Occurrence
    protect: bool


class ProtectEntity(StrictIdentity):
    kind: Literal["protect"] = "protect"
    expected_epoch: int = Field(ge=0)
    entity_id: IdentityID


class ConfirmAlias(StrictIdentity):
    kind: Literal["alias"] = "alias"
    expected_epoch: int = Field(ge=0)
    entity_id: IdentityID
    mention: Occurrence


IdentityCommand = Annotated[
    CreateEntity | ProtectEntity | ConfirmAlias, Field(discriminator="kind")
]


class DraftCreate(StrictIdentity):
    kind: Literal["create"] = "create"
    occurrence: DraftOccurrence
    protect: bool


class DraftAlias(StrictIdentity):
    kind: Literal["alias"] = "alias"
    entity_id: IdentityID
    occurrence: DraftOccurrence


DraftAction = Annotated[DraftCreate | DraftAlias, Field(discriminator="kind")]


class DraftIdentity(StrictIdentity):
    expected_epoch: int = Field(ge=0)
    text_digest: Digest
    actions: tuple[DraftAction, ...] = Field(min_length=1, max_length=16)

    @field_validator("actions", mode="before")
    @classmethod
    def freeze_actions(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


@dataclass(frozen=True)
class IdentityResult:
    state: State
    entity_id: str
    policy_fact_id: str | None


def _entity(state: State, entity_id: str) -> Entity:
    for entity in state.entities:
        if entity.id == entity_id:
            return entity
    raise PrivacyError("STATE_ENTITY_MISSING")


def _span(source: Source, occurrence: DraftOccurrence) -> SourceSpan:
    if (
        not 0 <= occurrence.start < occurrence.end <= len(source.text)
        or not occurrence.quote.strip()
        or source.text[occurrence.start : occurrence.end] != occurrence.quote
    ):
        raise PrivacyError("STATE_EVIDENCE_MISMATCH")
    return SourceSpan(source.id, source.revision, occurrence.start, occurrence.end)


def _committed_span(state: State, occurrence: Occurrence) -> SourceSpan:
    source = state.source(occurrence.source_id)
    if source.revision != occurrence.source_revision or source.digest != occurrence.source_digest:
        raise PrivacyError("STATE_STALE")
    return _span(source, occurrence)


def _protect(state: State, entity: Entity) -> IdentityResult:
    policies = tuple(f for f in state.facts if f.entity_id == entity.id and f.scope == "entity")
    if policies:
        strongest = next((f for f in policies if not f.restore_allowed), policies[0])
        return IdentityResult(state, entity.id, strongest.id)
    state, fact = state.add_fact(entity.evidence, "confidential", entity.id, scope="entity")
    return IdentityResult(state, entity.id, fact.id)


def apply_identity(state: State, command: IdentityCommand) -> IdentityResult:
    state.validate()
    if type(command) not in (CreateEntity, ProtectEntity, ConfirmAlias):
        raise PrivacyError("STATE_INVALID")
    if state.epoch != command.expected_epoch:
        raise PrivacyError("STATE_STALE")
    if isinstance(command, CreateEntity):
        staged, entity = state.add_entity(_committed_span(state, command.evidence))
        result = (
            _protect(staged, entity) if command.protect else IdentityResult(staged, entity.id, None)
        )
    elif isinstance(command, ProtectEntity):
        result = _protect(state, _entity(state, command.entity_id))
    else:
        entity = _entity(state, command.entity_id)
        staged = state.add_link(
            Link(_committed_span(state, command.mention), entity.id, 1, "alias", entity.evidence)
        )
        policy = next(
            (f.id for f in staged.facts if f.entity_id == entity.id and f.scope == "entity"), None
        )
        result = IdentityResult(staged, entity.id, policy)
    result.state.validate()
    return result


def apply_draft_identity(
    base: State, staged: State, current: Source, draft: DraftIdentity
) -> State:
    base.validate()
    staged.validate()
    if type(draft) is not DraftIdentity or type(current) is not Source:
        raise PrivacyError("STATE_INVALID")
    if draft.expected_epoch != base.epoch or draft.text_digest != current.digest:
        raise PrivacyError("STATE_STALE")
    if current.id in {source.id for source in base.sources} or staged != replace(
        base, sources=(*base.sources, current), epoch=base.epoch + 1
    ):
        raise PrivacyError("STATE_STALE")
    state = staged
    targets = {entity.id for entity in base.entities}
    assigned: list[tuple[SourceSpan, str]] = []
    for index, action in enumerate(draft.actions):
        span = _span(current, action.occurrence)
        target = action.entity_id if isinstance(action, DraftAlias) else f"new:{index}"
        if any(
            old.start < span.end and span.start < old.end and target != old_target
            for old, old_target in assigned
        ):
            raise PrivacyError("STATE_AMBIGUOUS_LINK")
        assigned.append((span, target))
        if isinstance(action, DraftCreate):
            state, entity = state.add_entity(span)
            if action.protect:
                state = _protect(state, entity).state
        else:
            if action.entity_id not in targets:
                raise PrivacyError("STATE_ENTITY_MISSING")
            entity = _entity(base, action.entity_id)
            state = state.add_link(Link(span, entity.id, 1, "alias", entity.evidence))
    state.validate()
    return state


def verify_draft_identity(
    base: State, staged: State, current: Source, draft: DraftIdentity
) -> None:
    staged.validate()
    source_only = replace(base, sources=(*base.sources, current), epoch=base.epoch + 1)
    expected = apply_draft_identity(base, source_only, current, draft)
    for entity in expected.entities:
        if entity in base.entities:
            continue
        actual = next((e for e in staged.entities if e.evidence == entity.evidence), None)
        if actual is None or actual in base.entities:
            raise PrivacyError("STATE_IDENTITY_NOT_APPLIED")
        if any(
            f.scope == "entity" and f.entity_id == entity.id for f in expected.facts
        ) and not any(f.scope == "entity" and f.entity_id == actual.id for f in staged.facts):
            raise PrivacyError("STATE_IDENTITY_NOT_APPLIED")
    if not set(expected.links).issubset(staged.links):
        raise PrivacyError("STATE_IDENTITY_NOT_APPLIED")
