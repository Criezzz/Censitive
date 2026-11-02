from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from privacy_gateway.domain.models import SourceSpan
from privacy_gateway.domain.redaction import Binding, PrivacyError
from privacy_gateway.domain.serialization import strict_json
from privacy_gateway.domain.state import Entity, Fact, Link, Source, State

if TYPE_CHECKING:
    from privacy_gateway.application.gateway import CommandReceipt, Message, Session


RECORD_VERSION = 1
ProviderStatus = Literal["confirmed", "unavailable"]
RecoveryStatus = Literal["none", "unknown_send", "provider_unavailable", "storage_failed"]
ProcessingMode = Literal["rules", "semantic"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SpanRecord(_StrictModel):
    message_id: str = Field(min_length=1, max_length=128)
    message_revision: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    def to_domain(self) -> SourceSpan:
        try:
            return SourceSpan(self.message_id, self.message_revision, self.start, self.end)
        except (TypeError, ValueError):
            raise PrivacyError("SESSION_RECORD_INVALID") from None

    @classmethod
    def from_domain(cls, value: SourceSpan) -> SpanRecord:
        return cls(
            message_id=value.message_id,
            message_revision=value.message_revision,
            start=value.start,
            end=value.end,
        )


class SourceRecord(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20_000)
    revision: int = Field(ge=1)

    def to_domain(self) -> Source:
        return Source(self.id, self.text, self.revision)

    @classmethod
    def from_domain(cls, value: Source) -> SourceRecord:
        return cls(id=value.id, text=value.text, revision=value.revision)


class EntityRecord(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    evidence: SpanRecord

    def to_domain(self) -> Entity:
        return Entity(self.id, self.evidence.to_domain())

    @classmethod
    def from_domain(cls, value: Entity) -> EntityRecord:
        return cls(id=value.id, evidence=SpanRecord.from_domain(value.evidence))


class FactRecord(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    assertion: SpanRecord
    category: Literal["personal", "health", "financial", "credential", "confidential"]
    entity_id: str | None = None
    superseded: bool = False
    scope: Literal["fact", "entity"] = "fact"

    def to_domain(self) -> Fact:
        return Fact(
            self.id,
            self.revision,
            self.assertion.to_domain(),
            self.category,
            self.entity_id,
            self.superseded,
            self.scope,
        )

    @classmethod
    def from_domain(cls, value: Fact) -> FactRecord:
        return cls(
            id=value.id,
            revision=value.revision,
            assertion=SpanRecord.from_domain(value.assertion),
            category=value.category,
            entity_id=value.entity_id,
            superseded=value.superseded,
            scope=value.scope,
        )


class LinkRecord(_StrictModel):
    mention: SpanRecord
    target: str = Field(min_length=1, max_length=128)
    target_revision: int = Field(ge=1)
    relation: Literal["reference", "paraphrase", "alias"]
    evidence: SpanRecord

    def to_domain(self) -> Link:
        return Link(
            self.mention.to_domain(),
            self.target,
            self.target_revision,
            self.relation,
            self.evidence.to_domain(),
        )

    @classmethod
    def from_domain(cls, value: Link) -> LinkRecord:
        return cls(
            mention=SpanRecord.from_domain(value.mention),
            target=value.target,
            target_revision=value.target_revision,
            relation=value.relation,
            evidence=SpanRecord.from_domain(value.evidence),
        )


class StateRecord(_StrictModel):
    sources: list[SourceRecord] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)
    links: list[LinkRecord] = Field(default_factory=list)
    epoch: int = Field(default=0, ge=0)
    protection_epoch: int = Field(default=0, ge=0)
    rule_denials: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, value: State) -> StateRecord:
        value.validate()
        return cls(
            sources=[SourceRecord.from_domain(item) for item in value.sources],
            entities=[EntityRecord.from_domain(item) for item in value.entities],
            facts=[FactRecord.from_domain(item) for item in value.facts],
            links=[LinkRecord.from_domain(item) for item in value.links],
            epoch=value.epoch,
            protection_epoch=value.protection_epoch,
            rule_denials=list(value.rule_denials),
        )

    def to_domain(self) -> State:
        try:
            state = State(
                sources=tuple(item.to_domain() for item in self.sources),
                entities=tuple(item.to_domain() for item in self.entities),
                facts=tuple(item.to_domain() for item in self.facts),
                links=tuple(item.to_domain() for item in self.links),
                epoch=self.epoch,
                protection_epoch=self.protection_epoch,
                rule_denials=tuple(self.rule_denials),
            )
            state.validate()
            return state
        except (TypeError, ValueError, PrivacyError):
            raise PrivacyError("SESSION_RECORD_INVALID") from None


class MessageRecord(_StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=200_000)
    provider: str = Field(default="", max_length=128)
    source_id: str = Field(default="", max_length=128)

    @classmethod
    def from_domain(cls, value: Message) -> MessageRecord:
        return cls(
            role=cast(Literal["user", "assistant"], value.role),
            content=value.content,
            provider=value.provider,
            source_id=value.source_id,
        )


class BindingRecord(_StrictModel):
    token: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=20_000)
    restore_allowed: bool = True
    scope: str = Field(default="", max_length=128)

    @classmethod
    def from_domain(cls, value: Binding) -> BindingRecord:
        return cls(
            token=value.token,
            value=value.value,
            restore_allowed=value.restore_allowed,
            scope=value.scope,
        )

    def to_domain(self) -> Binding:
        return Binding(self.token, self.value, self.restore_allowed, self.scope)


class CommandRecord(_StrictModel):
    signature: str = Field(min_length=1, max_length=128)
    committed_epoch: int = Field(ge=0)
    entity_id: str = Field(default="", max_length=128)
    policy_fact_id: str | None = None
    fact_id: str = Field(default="", max_length=128)
    fact_revision: int = Field(default=0, ge=0)
    old_prepared_id: str = Field(default="", max_length=128)
    new_prepared_id: str = Field(default="", max_length=128)
    body_sha256: str = Field(default="", max_length=128)

    @classmethod
    def from_domain(cls, value: CommandReceipt) -> CommandRecord:
        return cls(
            signature=value.signature,
            committed_epoch=value.committed_epoch,
            entity_id=value.entity_id,
            policy_fact_id=value.policy_fact_id,
            fact_id=value.fact_id,
            fact_revision=value.fact_revision,
            old_prepared_id=value.old_prepared_id,
            new_prepared_id=value.new_prepared_id,
            body_sha256=value.body_sha256,
        )

    def to_domain(self) -> CommandReceipt:
        from privacy_gateway.application.gateway import CommandReceipt

        return CommandReceipt(
            self.signature,
            self.committed_epoch,
            self.entity_id,
            self.policy_fact_id,
            self.fact_id,
            self.fact_revision,
            self.old_prepared_id,
            self.new_prepared_id,
            self.body_sha256,
        )


class ProviderRecord(_StrictModel):
    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    serializer_version: str = Field(min_length=1, max_length=128)
    status: ProviderStatus = "confirmed"


class SendMarkerRecord(_StrictModel):
    marker_id: str = Field(min_length=1, max_length=128)
    prepared_id: str = Field(min_length=1, max_length=128)
    request_sha256: str = Field(pattern=r"[0-9a-f]{64}")
    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    serializer_version: str = Field(min_length=1, max_length=128)
    original: str = Field(default="", max_length=20_000)
    status: Literal["may_have_been_sent"] = "may_have_been_sent"


class SessionRecord(_StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"[0-9a-f]{32}")
    owner: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=120)
    version: int = Field(default=0, ge=0)
    provider: ProviderRecord
    provider_confirmed: bool = True
    recovery_status: RecoveryStatus = "none"
    processing_mode: ProcessingMode = "rules"
    private_history: list[MessageRecord] = Field(default_factory=list)
    provider_history: list[MessageRecord] = Field(default_factory=list)
    vault: list[BindingRecord] = Field(default_factory=list)
    state: StateRecord = Field(default_factory=StateRecord)
    commands: dict[str, CommandRecord] = Field(default_factory=dict)
    terms: list[str] = Field(default_factory=list)
    literal_policies: list[str] = Field(default_factory=list)
    send_marker: SendMarkerRecord | None = None

    @field_validator("commands")
    @classmethod
    def command_keys_are_ids(cls, value: dict[str, CommandRecord]) -> dict[str, CommandRecord]:
        if len(value) > 128 or any(not key or len(key) > 256 for key in value):
            raise ValueError("invalid command map")
        return value

    def json_bytes(self) -> bytes:
        return self.model_dump_json(by_alias=False, exclude_none=False).encode("utf-8")

    @classmethod
    def parse_json(cls, raw: str | bytes) -> SessionRecord:
        try:
            value = strict_json(raw, code="SESSION_RECORD_INVALID")
            return cls.model_validate(value)
        except (ValidationError, ValueError, TypeError):
            raise PrivacyError("SESSION_RECORD_INVALID") from None


type SessionRecordInput = SessionRecord | Mapping[str, object] | str | bytes


class SessionStore(Protocol):
    def load(self, owner: str, session_id: str) -> SessionRecordInput | None: ...

    def save(self, record: SessionRecord) -> None: ...

    def delete(self, owner: str, session_id: str) -> None: ...

    def list(self, owner: str) -> Sequence[SessionRecordInput]: ...


class _OpaqueConversationStore(Protocol):
    def get(self, namespace: str, record_id: str) -> object | None: ...

    def put(self, namespace: str, record_id: str, data: Mapping[str, object]) -> object: ...

    def delete(self, namespace: str, record_id: str) -> bool: ...

    def list(self, namespace: str) -> Sequence[object]: ...


class EncryptedSessionRepository:
    def __init__(self, store: object, *, namespace: str = "sessions") -> None:
        self.store: _OpaqueConversationStore = cast(_OpaqueConversationStore, store)
        self.namespace = namespace

    def _owner_namespace(self, owner: str) -> str:
        if type(owner) is not str or not owner or len(owner) > 256:
            raise PrivacyError("SESSION_NOT_FOUND")
        return f"{self.namespace}:{owner}"

    @staticmethod
    def _data(row: object) -> SessionRecordInput:
        return cast(SessionRecordInput, getattr(row, "data", row))

    def load(self, owner: str, session_id: str) -> SessionRecordInput | None:
        row = self.store.get(self._owner_namespace(owner), session_id)
        return None if row is None else self._data(row)

    def save(self, record: SessionRecord) -> None:
        self.store.put(
            self._owner_namespace(record.owner),
            record.id,
            record.model_dump(mode="json"),
        )

    def delete(self, owner: str, session_id: str) -> None:
        self.store.delete(self._owner_namespace(owner), session_id)

    def list(self, owner: str) -> Sequence[SessionRecordInput]:
        rows = self.store.list(self._owner_namespace(owner))
        return tuple(self._data(item) for item in rows)


def decode_record(raw: SessionRecordInput) -> SessionRecord:
    if isinstance(raw, SessionRecord):
        return raw
    if isinstance(raw, (str, bytes)):
        return SessionRecord.parse_json(raw)
    try:
        return SessionRecord.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        raise PrivacyError("SESSION_RECORD_INVALID") from None


def session_record(
    session: Session, *, send_marker: SendMarkerRecord | None = None
) -> SessionRecord:
    provider_status: ProviderStatus = "confirmed" if session.provider_confirmed else "unavailable"
    marker = send_marker if send_marker is not None else session.send_marker
    return SessionRecord(
        id=session.id,
        owner=session.owner,
        title=session.title,
        version=session.version,
        provider=ProviderRecord(
            provider_id=session.provider.provider_id,
            model=session.provider.model,
            serializer_version=session.provider.serializer_version,
            status=provider_status,
        ),
        provider_confirmed=session.provider_confirmed,
        recovery_status=cast(RecoveryStatus, session.recovery_status),
        processing_mode=cast(ProcessingMode, session.processing_mode),
        private_history=[MessageRecord.from_domain(item) for item in session.private_history],
        provider_history=[MessageRecord.from_domain(item) for item in session.provider_history],
        vault=[BindingRecord.from_domain(item) for item in session.vault.values()],
        state=StateRecord.from_domain(session.state),
        commands={key: CommandRecord.from_domain(item) for key, item in session.commands.items()},
        terms=list(session.terms),
        literal_policies=list(session.literal_policies),
        send_marker=marker,
    )


def record_digest(record: SessionRecord) -> str:
    return hashlib.sha256(record.json_bytes()).hexdigest()


@dataclass(frozen=True)
class RecoveryInfo:
    status: RecoveryStatus
    provider_id: str
    model: str
    provider_confirmed: bool
    marker_id: str | None = None


__all__ = [
    "BindingRecord",
    "CommandRecord",
    "EntityRecord",
    "FactRecord",
    "LinkRecord",
    "MessageRecord",
    "ProcessingMode",
    "ProviderRecord",
    "RecoveryInfo",
    "RecoveryStatus",
    "RECORD_VERSION",
    "SendMarkerRecord",
    "SessionRecord",
    "SessionRecordInput",
    "SessionStore",
    "EncryptedSessionRepository",
    "SourceRecord",
    "SpanRecord",
    "StateRecord",
    "decode_record",
    "record_digest",
    "session_record",
]
