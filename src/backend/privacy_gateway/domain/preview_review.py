from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from privacy_gateway.domain.detection import detect
from privacy_gateway.domain.identity import DraftIdentity, verify_draft_identity
from privacy_gateway.domain.manual_facts import DraftFacts, verify_draft_facts
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.state import ID, Fact, State


class RejectNewMask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["reject_new_mask"]
    candidate_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class ReviewPlan:
    base: State
    trusted: State
    proposed: State
    source_id: str
    draft_identity: DraftIdentity | None = None
    draft_facts: DraftFacts | None = None
    rejected: tuple[str, ...] = ()


def _contains(outer: State, inner: State) -> bool:
    return all(
        set(getattr(inner, name)) <= set(getattr(outer, name))
        for name in ("sources", "entities", "facts", "links", "rule_denials")
    )


def _validate(plan: ReviewPlan, base: State) -> None:
    if type(plan) is not ReviewPlan or plan.base != base:
        raise PrivacyError("STATE_STALE")
    for state in (base, plan.trusted, plan.proposed):
        state.validate()
    source = plan.proposed.source(plan.source_id)
    if (
        not _contains(plan.trusted, base)
        or not _contains(plan.proposed, plan.trusted)
        or plan.trusted.sources != (*base.sources, source)
        or plan.proposed.sources != plan.trusted.sources
    ):
        raise PrivacyError("STATE_OBLIGATION_REMOVED")
    if plan.draft_identity is not None:
        verify_draft_identity(base, plan.trusted, source, plan.draft_identity)
    if plan.draft_facts is not None:
        verify_draft_facts(base, plan.trusted, source, plan.draft_facts)


def _reason(plan: ReviewPlan, fact: Fact, literals: tuple[str, ...]) -> str:
    state = plan.proposed
    span = fact.assertion
    if fact in plan.trusted.facts:
        return "confirmed_protection"
    if span.message_id != plan.source_id:
        return "historical_source"
    if fact.scope != "fact" or fact.entity_id is not None or fact.superseded or fact.revision != 1:
        return "identity_or_revision"
    if not fact.restore_allowed:
        return "credential"
    if any(link.target == fact.id for link in state.links):
        return "dependent_reference"
    source = state.source(span.message_id)
    for value in state.denied_literals:
        offset = 0
        while (start := source.text.find(value, offset)) >= 0:
            if start < span.end and span.start < start + len(value):
                return "credential"
            offset = start + 1
    spans = [other.assertion for other in state.facts if other != fact]
    spans += [link.mention for link in state.links]
    spans += [
        replace(span, start=item.start, end=item.end)
        for item in detect(source.text, literals, merge_overlaps=True)
    ]
    if any(
        other.message_id == span.message_id
        and other.message_revision == span.message_revision
        and other.start < span.end
        and span.start < other.end
        for other in spans
    ):
        return "overlapping_protection"
    return ""


def reviewed_state(plan: ReviewPlan, base: State, literals: tuple[str, ...]) -> State:
    _validate(plan, base)
    if len(set(plan.rejected)) != len(plan.rejected) or len(plan.rejected) > 128:
        raise PrivacyError("PREVIEW_DECISION_INVALID")
    for candidate in plan.rejected:
        fact = plan.proposed.fact(candidate, 1)
        if _reason(plan, fact, literals):
            raise PrivacyError("PREVIEW_DECISION_LOCKED")
    if not plan.rejected:
        return plan.proposed
    state = replace(
        plan.proposed,
        facts=tuple(f for f in plan.proposed.facts if f.id not in plan.rejected),
        epoch=plan.proposed.epoch + len(plan.rejected),
        protection_epoch=plan.proposed.protection_epoch + len(plan.rejected),
    )
    state.validate()
    if not _contains(state, plan.trusted):
        raise PrivacyError("STATE_OBLIGATION_REMOVED")
    return state


def review_items(plan: ReviewPlan, literals: tuple[str, ...]) -> list[dict[str, object]]:
    reviewed_state(plan, plan.base, literals)
    source = plan.proposed.source(plan.source_id)
    return [
        {
            "candidate_id": fact.id,
            "source_id": source.id,
            "source_revision": source.revision,
            "source_digest": source.digest,
            "start": fact.assertion.start,
            "end": fact.assertion.end,
            "quote": plan.proposed.quote(fact.assertion),
            "can_reject": fact.id not in plan.rejected and not _reason(plan, fact, literals),
            "locked_reason": _reason(plan, fact, literals),
            "rejected": fact.id in plan.rejected,
        }
        for fact in plan.proposed.facts
        if fact.assertion.message_id == source.id
    ]


def reject_new_mask(
    plan: ReviewPlan, base: State, literals: tuple[str, ...], command: RejectNewMask
) -> tuple[ReviewPlan, State]:
    reviewed_state(plan, base, literals)
    if (
        type(command) is not RejectNewMask
        or type(command.candidate_id) is not str
        or not ID.fullmatch(command.candidate_id)
        or command.kind != "reject_new_mask"
        or command.candidate_id in plan.rejected
    ):
        raise PrivacyError("PREVIEW_DECISION_INVALID")
    updated = replace(plan, rejected=(*plan.rejected, command.candidate_id))
    return updated, reviewed_state(updated, base, literals)
