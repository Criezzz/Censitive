import hashlib
import re
import secrets
from dataclasses import dataclass, replace
from typing import Literal

from privacy_gateway.domain.detection import credential_detections
from privacy_gateway.domain.models import SourceSpan, validate_span
from privacy_gateway.domain.redaction import PREFIX, PrivacyError
from privacy_gateway.domain.semantic import Category

MAX_SOURCES = 80
MAX_FACTS = 128
MAX_LINKS = 512
ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Source:
    id: str
    text: str
    revision: int = 1

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entity:
    id: str
    evidence: SourceSpan


@dataclass(frozen=True)
class Fact:
    id: str
    revision: int
    assertion: SourceSpan
    category: Category
    entity_id: str | None = None
    superseded: bool = False
    scope: Literal["fact", "entity"] = "fact"

    @property
    def restore_allowed(self) -> bool:
        return self.category != "credential"


@dataclass(frozen=True)
class Link:
    mention: SourceSpan
    target: str
    target_revision: int
    relation: Literal["reference", "paraphrase", "alias"]
    evidence: SourceSpan


@dataclass(frozen=True)
class State:
    sources: tuple[Source, ...] = ()
    entities: tuple[Entity, ...] = ()
    facts: tuple[Fact, ...] = ()
    links: tuple[Link, ...] = ()
    epoch: int = 0
    protection_epoch: int = 0
    rule_denials: tuple[str, ...] = ()

    def protect_credentials(self, texts: tuple[str, ...]) -> "State":
        values = set(self.rule_denials)
        for text in texts:
            values.update(text[d.start : d.end] for d in credential_detections(text))
        if len(values) > 512 or sum(len(value) for value in values) > 20_000:
            raise PrivacyError("STATE_LIMIT")
        result = tuple(sorted(values))
        return (
            self
            if result == self.rule_denials
            else replace(
                self,
                rule_denials=result,
                epoch=self.epoch + 1,
                protection_epoch=self.protection_epoch + 1,
            )
        )

    @property
    def denied_literals(self) -> tuple[str, ...]:
        values = {self.quote(f.assertion) for f in self.facts if not f.restore_allowed}
        values.update(self.rule_denials)
        for source in self.sources:
            values.update(source.text[d.start : d.end] for d in credential_detections(source.text))
        if len(values) > 512 or sum(len(value) for value in values) > 20_000:
            raise PrivacyError("STATE_LIMIT")
        return tuple(sorted(values))

    @property
    def protection_digest(self) -> str:
        evidence_ids = {f.assertion.message_id for f in self.facts}
        evidence_ids.update(e.evidence.message_id for e in self.entities)
        evidence_ids.update(
            span.message_id for link in self.links for span in (link.mention, link.evidence)
        )
        evidence = tuple((key, self.source(key).digest) for key in sorted(evidence_ids))
        return hashlib.sha256(
            repr((self.entities, self.facts, self.links, evidence, self.denied_literals)).encode()
        ).hexdigest()

    def source(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise PrivacyError("STATE_SOURCE_MISSING")

    def quote(self, span: SourceSpan) -> str:
        if type(span) is not SourceSpan:
            raise PrivacyError("STATE_INVALID")
        source = self.source(span.message_id)
        validate_span(
            span, text=source.text, message_id=source.id, message_revision=source.revision
        )
        return source.text[span.start : span.end]

    def fact(self, fact_id: str, revision: int | None = None) -> Fact:
        if revision is not None and (type(revision) is not int or revision < 1):
            raise PrivacyError("STATE_INVALID")
        for fact in self.facts:
            if fact.id == fact_id and (
                fact.revision == revision if revision is not None else not fact.superseded
            ):
                return fact
        raise PrivacyError("STATE_FACT_MISSING")

    def add_source(self, text: str) -> tuple["State", Source]:
        if len(self.sources) >= MAX_SOURCES:
            raise PrivacyError("STATE_LIMIT")
        if type(text) is not str or not text.strip() or len(text) > 20_000 or PREFIX in text:
            raise PrivacyError("INVALID_INPUT")
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise PrivacyError("INVALID_INPUT") from None
        source = Source(secrets.token_hex(16), text)
        return replace(self, sources=(*self.sources, source), epoch=self.epoch + 1), source

    def add_entity(self, evidence: SourceSpan) -> tuple["State", Entity]:
        if not self.quote(evidence).strip():
            raise PrivacyError("STATE_INVALID")
        if len(self.entities) >= MAX_FACTS:
            raise PrivacyError("STATE_LIMIT")
        entity = Entity(secrets.token_hex(16), evidence)
        self._check_identity_occurrence(evidence, entity.id)
        return replace(
            self,
            entities=(*self.entities, entity),
            epoch=self.epoch + 1,
            protection_epoch=self.protection_epoch + 1,
        ), entity

    def _check_identity_occurrence(self, mention: SourceSpan, target: str) -> None:
        assignments = [(entity.evidence, entity.id) for entity in self.entities]
        assignments.extend(
            (link.mention, link.target) for link in self.links if link.relation == "alias"
        )
        if any(
            other != target
            and span.message_id == mention.message_id
            and span.message_revision == mention.message_revision
            and span.start < mention.end
            and mention.start < span.end
            for span, other in assignments
        ):
            raise PrivacyError("STATE_AMBIGUOUS_LINK")

    def add_fact(
        self,
        assertion: SourceSpan,
        category: Category,
        entity_id: str | None = None,
        *,
        scope: Literal["fact", "entity"] = "fact",
    ) -> tuple["State", Fact]:
        if not self.quote(assertion).strip():
            raise PrivacyError("STATE_INVALID")
        if len(self.facts) >= MAX_FACTS:
            raise PrivacyError("STATE_LIMIT")
        if type(category) is not str or category not in {
            "personal",
            "health",
            "financial",
            "credential",
            "confidential",
        }:
            raise PrivacyError("STATE_CATEGORY_INVALID")
        if entity_id is not None and not any(entity.id == entity_id for entity in self.entities):
            raise PrivacyError("STATE_ENTITY_MISSING")
        if (
            type(scope) is not str
            or scope not in {"fact", "entity"}
            or (
                scope == "entity"
                and not any(
                    entity.id == entity_id and entity.evidence == assertion
                    for entity in self.entities
                )
            )
        ):
            raise PrivacyError("STATE_SCOPE_INVALID")
        fact = Fact(secrets.token_hex(16), 1, assertion, category, entity_id, scope=scope)
        return replace(
            self,
            facts=(*self.facts, fact),
            epoch=self.epoch + 1,
            protection_epoch=self.protection_epoch + 1,
        ), fact

    def add_link(self, link: Link) -> "State":
        if (
            type(link) is not Link
            or type(link.target_revision) is not int
            or link.target_revision < 1
        ):
            raise PrivacyError("STATE_INVALID")
        if not self.quote(link.mention).strip() or not self.quote(link.evidence).strip():
            raise PrivacyError("STATE_INVALID")
        if type(link.relation) is not str or link.relation not in {
            "reference",
            "paraphrase",
            "alias",
        }:
            raise PrivacyError("STATE_RELATION_INVALID")
        if link.relation == "alias":
            entity = next((e for e in self.entities if e.id == link.target), None)
            if entity is None or link.target_revision != 1 or link.evidence != entity.evidence:
                raise PrivacyError("STATE_EVIDENCE_MISMATCH")
            self._check_identity_occurrence(link.mention, link.target)
        else:
            fact = self.fact(link.target, link.target_revision)
            if link.evidence != fact.assertion:
                raise PrivacyError("STATE_EVIDENCE_MISMATCH")
            if fact.scope == "entity" and (
                link.mention == fact.assertion
                or any(
                    old.relation == "alias"
                    and old.target == fact.entity_id
                    and old.mention == link.mention
                    for old in self.links
                )
            ):
                return self
        if any(
            (old.target, old.target_revision) != (link.target, link.target_revision)
            and not (
                link.relation == "alias"
                and old.relation != "alias"
                and old.mention == link.mention
                and any(
                    fact.id == old.target
                    and fact.revision == old.target_revision
                    and fact.scope == "entity"
                    and fact.entity_id == link.target
                    for fact in self.facts
                )
            )
            and (
                old.mention == link.mention
                or (
                    (old.relation == "alias" or link.relation == "alias")
                    and old.mention.message_id == link.mention.message_id
                    and old.mention.message_revision == link.mention.message_revision
                    and old.mention.start < link.mention.end
                    and link.mention.start < old.mention.end
                )
            )
            for old in self.links
        ):
            raise PrivacyError("STATE_AMBIGUOUS_LINK")
        if link in self.links:
            return self
        if len(self.links) >= MAX_LINKS:
            raise PrivacyError("STATE_LIMIT")
        return replace(
            self,
            links=(*self.links, link),
            epoch=self.epoch + 1,
            protection_epoch=self.protection_epoch + 1,
        )

    def correct(
        self, fact_id: str, expected_revision: int, expected_epoch: int, assertion: SourceSpan
    ) -> "State":
        if type(expected_epoch) is not int or expected_epoch != self.epoch:
            raise PrivacyError("STATE_STALE")
        old = self.fact(fact_id)
        if old.scope == "entity":
            raise PrivacyError("STATE_IDENTITY_REVIEW_REQUIRED")
        if type(expected_revision) is not int or old.revision != expected_revision:
            raise PrivacyError("STATE_STALE")
        if not self.quote(assertion).strip():
            raise PrivacyError("STATE_INVALID")
        if assertion == old.assertion:
            raise PrivacyError("STATE_CORRECTION_UNCHANGED")
        if len(self.facts) >= MAX_FACTS:
            raise PrivacyError("STATE_LIMIT")
        current = replace(old, assertion=assertion, revision=old.revision + 1)
        facts = tuple(replace(f, superseded=True) if f == old else f for f in self.facts)
        return replace(
            self,
            facts=(*facts, current),
            epoch=self.epoch + 1,
            protection_epoch=self.protection_epoch + 1,
        )

    def validate(self) -> None:
        if (
            type(self.rule_denials) is not tuple
            or len(self.rule_denials) > 512
            or any(
                type(value) is not str or not value.strip() or PREFIX in value
                for value in self.rule_denials
            )
            or sum(len(value) for value in self.rule_denials) > 20_000
        ):
            raise PrivacyError("STATE_INVALID")
        if any(
            type(items) is not tuple
            for items in (self.sources, self.entities, self.facts, self.links)
        ):
            raise PrivacyError("STATE_INVALID")
        if (
            type(self.epoch) is not int
            or type(self.protection_epoch) is not int
            or not 0 <= self.protection_epoch <= self.epoch
        ):
            raise PrivacyError("STATE_INVALID")
        if (
            len(self.sources) > MAX_SOURCES
            or len(self.facts) > MAX_FACTS
            or len(self.links) > MAX_LINKS
            or len(self.entities) > MAX_FACTS
        ):
            raise PrivacyError("STATE_LIMIT")
        source_ids: set[str] = set()
        for source in self.sources:
            if (
                type(source) is not Source
                or type(source.id) is not str
                or not ID.fullmatch(source.id)
                or source.id in source_ids
                or type(source.revision) is not int
                or source.revision < 1
                or type(source.text) is not str
                or not source.text.strip()
                or len(source.text) > 20_000
                or PREFIX in source.text
            ):
                raise PrivacyError("STATE_INVALID")
            try:
                source.text.encode("utf-8")
            except UnicodeError:
                raise PrivacyError("STATE_INVALID") from None
            source_ids.add(source.id)
        entity_ids: set[str] = set()
        for entity in self.entities:
            if (
                type(entity) is not Entity
                or type(entity.id) is not str
                or not ID.fullmatch(entity.id)
                or entity.id in entity_ids
                or not self.quote(entity.evidence).strip()
            ):
                raise PrivacyError("STATE_INVALID")
            entity_ids.add(entity.id)
        groups: dict[str, list[Fact]] = {}
        for fact in self.facts:
            if (
                type(fact) is not Fact
                or type(fact.id) is not str
                or not ID.fullmatch(fact.id)
                or type(fact.revision) is not int
                or fact.revision < 1
                or type(fact.superseded) is not bool
                or type(fact.category) is not str
                or fact.category
                not in {"personal", "health", "financial", "credential", "confidential"}
                or not self.quote(fact.assertion).strip()
            ):
                raise PrivacyError("STATE_INVALID")
            if fact.entity_id is not None and not any(
                e.id == fact.entity_id for e in self.entities
            ):
                raise PrivacyError("STATE_ENTITY_MISSING")
            groups.setdefault(fact.id, []).append(fact)
            if (
                type(fact.scope) is not str
                or fact.scope not in {"fact", "entity"}
                or (
                    fact.scope == "entity"
                    and not any(
                        entity.id == fact.entity_id and entity.evidence == fact.assertion
                        for entity in self.entities
                    )
                )
            ):
                raise PrivacyError("STATE_SCOPE_INVALID")
        for revisions in groups.values():
            ordered = sorted(revisions, key=lambda f: f.revision)
            if (
                [f.revision for f in ordered] != list(range(1, len(ordered) + 1))
                or any(not f.superseded for f in ordered[:-1])
                or ordered[-1].superseded
                or len({(f.category, f.entity_id, f.scope) for f in ordered}) != 1
            ):
                raise PrivacyError("STATE_INVALID")
        if source_ids & entity_ids or set(groups) & (source_ids | entity_ids):
            raise PrivacyError("STATE_INVALID")
        for link in self.links:
            if type(link) is not Link:
                raise PrivacyError("STATE_INVALID")
            self.quote(link.mention)
            self.quote(link.evidence)
        for link in self.links:
            self.add_link(link)
        for entity in self.entities:
            self._check_identity_occurrence(entity.evidence, entity.id)
        if len(set(self.links)) != len(self.links):
            raise PrivacyError("STATE_INVALID")


def unique_span(source: Source, quote: str) -> SourceSpan:
    if type(source) is not Source or type(quote) is not str:
        raise PrivacyError("STATE_INVALID")
    start = source.text.find(quote)
    if not quote.strip() or start < 0:
        raise PrivacyError("SEMANTIC_UNGROUNDED")
    if source.text.find(quote, start + 1) >= 0:
        raise PrivacyError("STATE_AMBIGUOUS_OCCURRENCE")
    return SourceSpan(source.id, source.revision, start, start + len(quote))


@dataclass(frozen=True)
class DecisionStamp:
    source_id: str
    source_revision: int
    source_digest: str
    protection_epoch: int
    resolver_signature: str
    protection_digest: str = ""


def dirty_sources(
    state: State, stamps: tuple[DecisionStamp, ...], signature: str
) -> tuple[str, ...]:
    state.validate()
    if any(
        type(s) is not DecisionStamp
        or type(s.source_revision) is not int
        or type(s.protection_epoch) is not int
        for s in stamps
    ) or len({s.source_id for s in stamps}) != len(stamps):
        raise PrivacyError("STATE_CACHE_INVALID")
    known = {stamp.source_id: stamp for stamp in stamps}
    return tuple(
        source.id
        for source in state.sources
        if known.get(source.id)
        != DecisionStamp(
            source.id,
            source.revision,
            source.digest,
            state.protection_epoch,
            signature,
            state.protection_digest,
        )
    )
