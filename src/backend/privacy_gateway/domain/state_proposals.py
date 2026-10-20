from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from privacy_gateway.domain.models import SourceSpan
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.semantic import Category
from privacy_gateway.domain.serialization import strict_json
from privacy_gateway.domain.state import Fact, Link, State, unique_span


class StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuoteRef(StrictProposal):
    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_revision: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=800)


class NewFactProposal(StrictProposal):
    assertion: QuoteRef
    category: Category
    evidence: QuoteRef


class FactLinkProposal(StrictProposal):
    mention: QuoteRef
    fact_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    fact_revision: int = Field(ge=1)
    relation: Literal["reference", "paraphrase"]
    evidence: QuoteRef


class StateProposals(StrictProposal):
    review_required: bool
    new_facts: list[NewFactProposal] = Field(max_length=32)
    links: list[FactLinkProposal] = Field(max_length=64)


class DirectFactProposals(StateProposals):
    links: list[FactLinkProposal] = Field(max_length=0)


class IndexedDirectFact(StrictProposal):
    kind: Literal["direct_value", "direct_assertion"]
    assertion: str = Field(min_length=1, max_length=800)
    category: Category
    evidence: str = Field(min_length=1, max_length=800)


class IndexedSourceCheck(StrictProposal):
    message: int = Field(ge=0)
    status: Literal["none", "new", "uncertain"]
    new_facts: list[IndexedDirectFact] = Field(max_length=32)


class IndexedExtractionProposals(StrictProposal):
    checks: list[IndexedSourceCheck] = Field(max_length=128)


class ReferenceMention(StrictProposal):
    mention: QuoteRef
    relation: Literal["reference", "paraphrase"]


class FactReferenceCheck(StrictProposal):
    fact_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    fact_revision: int = Field(ge=1)
    status: Literal["none", "linked", "uncertain"]
    mentions: list[ReferenceMention] = Field(max_length=32)


class ReferenceProposals(StrictProposal):
    review_required: bool
    checks: list[FactReferenceCheck] = Field(max_length=128)


class IndexedMention(StrictProposal):
    message: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=800)
    relation: Literal["reference", "paraphrase"]


class IndexedFactCheck(StrictProposal):
    fact: int = Field(ge=0)
    status: Literal["none", "linked", "uncertain"]
    mentions: list[IndexedMention] = Field(max_length=32)


class IndexedReferenceProposals(StrictProposal):
    review_required: bool
    checks: list[IndexedFactCheck] = Field(max_length=128)


@dataclass(frozen=True)
class ReferenceIndex:
    state: State
    sources: tuple[tuple[str, int, str], ...]
    targets: tuple[tuple[str, int], ...]

    @classmethod
    def build(cls, state: State) -> "ReferenceIndex":
        state.validate()
        return cls(
            state,
            tuple((source.id, source.revision, source.digest) for source in state.sources),
            tuple((fact.id, fact.revision) for fact in state.facts),
        )

    def validate(self, state: State) -> None:
        if self != self.build(state):
            raise PrivacyError("STATE_STALE")


def indexed_reference_schema(index: ReferenceIndex) -> dict[str, Any]:
    index.validate(index.state)
    schema = IndexedReferenceProposals.model_json_schema()
    schema["properties"]["checks"]["minItems"] = len(index.targets)
    schema["properties"]["checks"]["maxItems"] = len(index.targets)
    if index.targets:
        schema["$defs"]["IndexedFactCheck"]["properties"]["fact"]["enum"] = list(
            range(len(index.targets))
        )
    if index.sources:
        schema["$defs"]["IndexedMention"]["properties"]["message"]["enum"] = list(
            range(len(index.sources))
        )
    return schema


def indexed_extraction_schema(index: ReferenceIndex) -> dict[str, Any]:
    index.validate(index.state)
    schema = IndexedExtractionProposals.model_json_schema()
    schema["properties"]["checks"]["minItems"] = len(index.sources)
    schema["properties"]["checks"]["maxItems"] = len(index.sources)
    if index.sources:
        schema["$defs"]["IndexedSourceCheck"]["properties"]["message"]["enum"] = list(
            range(len(index.sources))
        )
    return schema


def reference_schema(state: State) -> dict[str, Any]:
    state.validate()
    schema = ReferenceProposals.model_json_schema()
    checks = schema["properties"]["checks"]
    checks["minItems"] = checks["maxItems"] = len(state.facts)
    if state.facts:
        definitions = schema["$defs"]
        alternatives = []
        for index, fact in enumerate(state.facts):
            item = deepcopy(definitions["FactReferenceCheck"])
            item["properties"]["fact_id"]["enum"] = [fact.id]
            item["properties"]["fact_revision"]["enum"] = [fact.revision]
            name = f"RetainedFactCheck{index}"
            definitions[name] = item
            alternatives.append({"$ref": f"#/$defs/{name}"})
        checks["items"] = {"anyOf": alternatives}
        definitions["QuoteRef"]["properties"]["source_id"]["enum"] = [
            source.id for source in state.sources
        ]
        definitions["QuoteRef"]["properties"]["source_revision"]["enum"] = sorted(
            {source.revision for source in state.sources}
        )
    return schema


@dataclass(frozen=True)
class GroundedFact:
    assertion: SourceSpan
    category: Category
    evidence: SourceSpan


@dataclass(frozen=True)
class GroundedProposals:
    facts: tuple[GroundedFact, ...]
    links: tuple[Link, ...]


@dataclass(frozen=True)
class StateDelta:
    base_state: State
    staged_state: State
    new_facts: tuple[Fact, ...]
    new_links: tuple[Link, ...]

    def apply(self, base: State) -> State:
        if base != self.base_state:
            raise PrivacyError("STATE_STALE")
        return self.staged_state


def _span(state: State, proposal: QuoteRef) -> SourceSpan:
    source = state.source(proposal.source_id)
    if source.revision != proposal.source_revision:
        raise PrivacyError("STATE_STALE")
    try:
        return unique_span(source, proposal.quote)
    except PrivacyError as error:
        if str(error) == "STATE_AMBIGUOUS_OCCURRENCE":
            raise PrivacyError("STATE_REVIEW_REQUIRED") from None
        raise


def ground_state_proposals(raw: str, state: State) -> GroundedProposals:
    state.validate()
    try:
        output = StateProposals.model_validate(strict_json(raw, code="SEMANTIC_INVALID_OUTPUT"))
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    if output.review_required:
        raise PrivacyError("STATE_REVIEW_REQUIRED")
    facts: list[GroundedFact] = []
    for item in output.new_facts:
        assertion, evidence = _span(state, item.assertion), _span(state, item.evidence)
        if (
            evidence.message_id != assertion.message_id
            or evidence.start > assertion.start
            or evidence.end < assertion.end
        ):
            raise PrivacyError("STATE_EVIDENCE_MISMATCH")
        facts.append(GroundedFact(assertion, item.category, evidence))
    links: list[Link] = []
    checked = state
    for link_item in output.links:
        target = state.fact(link_item.fact_id, link_item.fact_revision)
        mention, evidence = _span(state, link_item.mention), _span(state, link_item.evidence)
        if evidence != target.assertion:
            raise PrivacyError("STATE_EVIDENCE_MISMATCH")
        link = Link(mention, target.id, target.revision, link_item.relation, evidence)
        try:
            checked = checked.add_link(link)
        except PrivacyError as error:
            if str(error) == "STATE_AMBIGUOUS_LINK":
                raise PrivacyError("STATE_REVIEW_REQUIRED") from None
            raise
        links.append(link)
    return GroundedProposals(tuple(facts), tuple(dict.fromkeys(links)))


def ground_direct_facts(raw: str, state: State) -> GroundedProposals:
    state.validate()
    try:
        DirectFactProposals.model_validate(strict_json(raw, code="SEMANTIC_INVALID_OUTPUT"))
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    return ground_state_proposals(raw, state)


def ground_indexed_extraction(raw: str, state: State, index: ReferenceIndex) -> GroundedProposals:
    index.validate(state)
    try:
        output = IndexedExtractionProposals.model_validate(
            strict_json(raw, code="SEMANTIC_INVALID_OUTPUT")
        )
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    seen: set[int] = set()
    candidates: list[NewFactProposal] = []
    for check in output.checks:
        if (
            check.message >= len(index.sources)
            or check.message in seen
            or check.status == "uncertain"
            or (check.status == "none" and check.new_facts)
            or (check.status == "new" and not check.new_facts)
        ):
            raise PrivacyError("STATE_REVIEW_REQUIRED")
        seen.add(check.message)
        source_id, revision, _ = index.sources[check.message]
        for candidate in check.new_facts:
            candidates.append(
                NewFactProposal(
                    assertion=QuoteRef(
                        source_id=source_id, source_revision=revision, quote=candidate.assertion
                    ),
                    category=candidate.category,
                    evidence=QuoteRef(
                        source_id=source_id, source_revision=revision, quote=candidate.evidence
                    ),
                )
            )
    if seen != set(range(len(index.sources))):
        raise PrivacyError("STATE_REVIEW_REQUIRED")
    if len(candidates) > 32:
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT")
    return ground_direct_facts(
        DirectFactProposals(
            review_required=False, new_facts=candidates, links=[]
        ).model_dump_json(),
        state,
    )


def stage_proposals(state: State, proposal: GroundedProposals) -> State:
    staged = state
    for item in proposal.facts:
        existing = tuple(f for f in staged.facts if f.assertion == item.assertion)
        if existing:
            if any(f.category != item.category for f in existing):
                raise PrivacyError("STATE_REVIEW_REQUIRED")
            continue
        staged, _ = staged.add_fact(item.assertion, item.category, scope="fact")
    for link in proposal.links:
        staged = staged.add_link(link)
    staged.validate()
    return staged


def ground_reference_checks(raw: str, state: State) -> GroundedProposals:
    state.validate()
    try:
        output = ReferenceProposals.model_validate(strict_json(raw, code="SEMANTIC_INVALID_OUTPUT"))
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    if output.review_required:
        raise PrivacyError("STATE_REVIEW_REQUIRED")
    expected = {(fact.id, fact.revision) for fact in state.facts}
    seen: set[tuple[str, int]] = set()
    links: list[Link] = []
    checked = state
    for item in output.checks:
        target = state.fact(item.fact_id, item.fact_revision)
        identity = (target.id, target.revision)
        if (
            identity in seen
            or item.status == "uncertain"
            or (item.status == "none" and item.mentions)
            or (item.status == "linked" and not item.mentions)
        ):
            raise PrivacyError("STATE_REVIEW_REQUIRED")
        seen.add(identity)
        for proposal in item.mentions:
            mention = _span(state, proposal.mention)
            if mention == target.assertion:
                continue
            link = Link(mention, target.id, target.revision, proposal.relation, target.assertion)
            try:
                checked = checked.add_link(link)
            except PrivacyError as error:
                if str(error) == "STATE_AMBIGUOUS_LINK":
                    raise PrivacyError("STATE_REVIEW_REQUIRED") from None
                raise
            links.append(link)
    if seen != expected:
        raise PrivacyError("STATE_REVIEW_REQUIRED")
    return GroundedProposals((), tuple(dict.fromkeys(links)))


def ground_indexed_references(raw: str, state: State, index: ReferenceIndex) -> GroundedProposals:
    index.validate(state)
    try:
        output = IndexedReferenceProposals.model_validate(
            strict_json(raw, code="SEMANTIC_INVALID_OUTPUT")
        )
    except (ValidationError, ValueError):
        raise PrivacyError("SEMANTIC_INVALID_OUTPUT") from None
    checks = []
    for item in output.checks:
        if item.fact >= len(index.targets):
            raise PrivacyError("STATE_FACT_MISSING")
        fact_id, revision = index.targets[item.fact]
        mentions = []
        for mention in item.mentions:
            if mention.message >= len(index.sources):
                raise PrivacyError("STATE_SOURCE_MISSING")
            source_id, source_revision, _ = index.sources[mention.message]
            mentions.append(
                ReferenceMention(
                    mention=QuoteRef(
                        source_id=source_id, source_revision=source_revision, quote=mention.quote
                    ),
                    relation=mention.relation,
                )
            )
        checks.append(
            FactReferenceCheck(
                fact_id=fact_id, fact_revision=revision, status=item.status, mentions=mentions
            )
        )
    typed = ReferenceProposals(review_required=output.review_required, checks=checks)
    return ground_reference_checks(typed.model_dump_json(), state)
