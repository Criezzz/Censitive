from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.adapters.local_detector import SemanticDetector
from privacy_gateway.adapters.provider import (
    Provider,
    ProviderNotDispatched,
    StreamingProvider,
    serialize_request,
)
from privacy_gateway.application.private_work import (
    JevGateBlocked,
    JevPreviewReady,
    PreparedWork,
    PrivateWorkbench,
    PrivateWorker,
    WorkResult,
)
from privacy_gateway.application.session_records import (
    PrivateWorkRecord,
    ProviderRecord,
    SendMarkerRecord,
    SessionStore,
    decode_record,
    session_record,
)
from privacy_gateway.domain.detection import Detection, credential_detections, detect
from privacy_gateway.domain.identity import (
    DraftIdentity,
    IdentityCommand,
    apply_identity,
    validate_identity_command,
    verify_draft_identity,
)
from privacy_gateway.domain.incremental import Compilation, CompiledSource, compile_state
from privacy_gateway.domain.jev import JevAssessment
from privacy_gateway.domain.limits import MAX_HISTORY_MESSAGES, MAX_REQUEST_BYTES
from privacy_gateway.domain.manual_facts import (
    CreateFact,
    DraftFacts,
    apply_manual_fact,
    validate_manual_fact,
    verify_draft_facts,
)
from privacy_gateway.domain.models import Replacement, SourceSpan
from privacy_gateway.domain.preview_review import (
    RejectNewMask,
    ReviewPlan,
    reject_new_mask,
    reviewed_state,
    validate_review_command,
)
from privacy_gateway.domain.private_ledger import (
    CURRENT_PARSER_VERSION,
    LedgerRevision,
    NoLedgerChanges,
    PrivateLedger,
    append_revision,
    current_revision,
    validate_revision,
)
from privacy_gateway.domain.redaction import (
    PREFIX,
    TOKEN,
    Binding,
    PrivacyError,
    apply_spans,
    restore_text,
    tokens_in,
    validate_tokens,
)
from privacy_gateway.domain.semantic import Protection, SemanticReport
from privacy_gateway.domain.serialization import strict_json
from privacy_gateway.domain.state import State, unique_span
from privacy_gateway.domain.streaming import IncrementalRestorer
from privacy_gateway.domain.structured_ledger import (
    StructuredLedgerValues,
    structured_revision,
    structured_source,
)

if TYPE_CHECKING:
    from privacy_gateway.adapters.state_resolver import Resolution, StateResolver

MAX_BODY = MAX_REQUEST_BYTES
MAX_SESSION_RECORD_BYTES = 4 * 1024 * 1024
PRIVATE_WORK_RECORD_RESERVE_BYTES = 256 * 1024


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    provider: str = ""
    source_id: str = ""


@dataclass(frozen=True)
class Prepared:
    id: str
    version: int
    body: bytes
    original: str
    sanitized: str
    detections: tuple[Detection, ...]
    manifest: Mapping[str, Binding]
    staged_vault: Mapping[str, Binding]
    expires_at: float
    provider_signature: tuple[str, str, str]
    semantic: SemanticReport | None = None
    request_id: str = ""
    staged_state: State | None = None
    source_id: str = ""
    literal_policies: tuple[str, ...] = ()
    compilation: Compilation | None = None
    expected_messages: tuple[Message, ...] = ()
    resolver_calls: int = 0
    resolver_full_fallback: bool = False
    review_plan: ReviewPlan | None = None


@dataclass(frozen=True)
class CommandReceipt:
    signature: str
    committed_epoch: int
    entity_id: str = ""
    policy_fact_id: str | None = None
    fact_id: str = ""
    fact_revision: int = 0
    old_prepared_id: str = ""
    new_prepared_id: str = ""
    body_sha256: str = ""


@dataclass(frozen=True)
class PrivateWork:
    id: str
    public_task: str
    private_context: str = field(repr=False)
    answer: str = field(repr=False)
    route: str
    public_body_sha256: str
    provider: str
    model: str
    trace: tuple[str, ...] = ()
    plan: dict[str, object] | None = field(default=None, repr=False)
    ledger_revision: int | None = None
    jev: JevAssessment | None = None


@dataclass(frozen=True)
class PendingLedger:
    id: str
    request_id: str
    candidate: LedgerRevision
    base_revision: int
    session_version: int
    expires_at: float


@dataclass
class Session:
    id: str
    owner: str
    provider: Provider = field(default_factory=FakeProvider)
    version: int = 0
    terms: tuple[str, ...] = ()
    private_history: list[Message] = field(default_factory=list)
    provider_history: list[Message] = field(default_factory=list)
    private_work_history: list[PrivateWork] = field(default_factory=list)
    private_ledger_history: list[LedgerRevision] = field(default_factory=list)
    vault: dict[str, Binding] = field(default_factory=dict)
    prepared: Prepared | None = None
    preparing_id: str | None = None
    cancelled_prepares: set[str] = field(default_factory=set)
    active_id: str | None = None
    active_task: asyncio.Task[object] | None = None
    cancelled: bool = False
    closed: bool = False
    audit: list[dict[str, str | int]] = field(default_factory=list)
    state: State = field(default_factory=State)
    compiled_sources: tuple[CompiledSource, ...] = ()
    literal_policies: tuple[str, ...] = ()
    commands: dict[str, CommandReceipt] = field(default_factory=dict)
    title: str = ""
    provider_confirmed: bool = True
    recovery_status: str = "none"
    processing_mode: str = "rules"
    send_marker: SendMarkerRecord | None = None
    persistence_failed: bool = False
    private_workbench: PrivateWorkbench | None = field(default=None, repr=False)
    private_prepared: PreparedWork | None = field(default=None, repr=False)
    private_prepared_version: int | None = field(default=None, repr=False)
    private_prepared_expires_at: float | None = field(default=None, repr=False)
    private_selected_signature: tuple[str, str, str] | None = field(default=None, repr=False)
    private_prepared_ledger_revision: int | None = field(default=None, repr=False)
    private_ledger_candidate: PendingLedger | None = field(default=None, repr=False)
    latest_jev: JevAssessment | None = None

    @property
    def busy(self) -> bool:
        return self.active_id is not None

    def record(self, event: str, status: str) -> None:
        self.audit.append({"event": event, "status": status, "version": self.version})
        self.audit[:] = self.audit[-100:]


class Gateway:
    def __init__(
        self,
        provider: Provider | None = None,
        detector: SemanticDetector | None = None,
        *,
        resolver: StateResolver | None = None,
        store: SessionStore | None = None,
        private_worker: PrivateWorker | None = None,
        ledger_proposer: object | None = None,
    ) -> None:
        self.sessions: dict[str, Session] = {}
        self.provider = provider or FakeProvider()
        self.detector = detector
        self.resolver = resolver
        self.store = store
        self.private_worker = private_worker
        self.ledger_proposer = ledger_proposer

    def _ensure_writable(self, session: Session, *, allow_unknown: bool = False) -> None:
        if session.closed:
            raise PrivacyError("SESSION_NOT_FOUND")
        if session.persistence_failed:
            raise PrivacyError("STORAGE_UNAVAILABLE")
        if session.recovery_status == "unknown_send" and not allow_unknown:
            raise PrivacyError("SEND_OUTCOME_UNKNOWN")

    def _persist(self, session: Session) -> None:
        if self.store is None:
            return
        if session.persistence_failed:
            raise PrivacyError("STORAGE_UNAVAILABLE")
        try:
            self.store.save(session_record(session))
        except Exception:
            session.persistence_failed = True
            session.recovery_status = "storage_failed"
            raise PrivacyError("STORAGE_UNAVAILABLE") from None

    @staticmethod
    def _unavailable_provider(identity: ProviderRecord) -> Provider:
        class UnavailableProvider:
            provider_id = identity.provider_id
            model = identity.model
            serializer_version = identity.serializer_version
            timeout = 0.0
            request_options: Mapping[str, object] = {}

            async def complete(self, body: bytes) -> str:
                raise PrivacyError("PROVIDER_UNAVAILABLE")

        return UnavailableProvider()

    def _restore(self, raw: object, owner: str) -> Session:
        record = decode_record(raw)  # type: ignore[arg-type]
        if not secrets.compare_digest(record.owner, owner):
            raise PrivacyError("SESSION_NOT_FOUND")
        provider = self._unavailable_provider(record.provider)
        session = Session(record.id, record.owner, provider=provider)
        session.title = record.title
        session.version = record.version
        session.provider_confirmed = False
        session.processing_mode = record.processing_mode
        session.private_history = [
            Message(item.role, item.content, item.provider, item.source_id)
            for item in record.private_history
        ]
        session.provider_history = [
            Message(item.role, item.content, item.provider, item.source_id)
            for item in record.provider_history
        ]
        session.private_work_history = [item.to_domain() for item in record.private_work_history]
        try:
            session.private_ledger_history = list(record.private_ledger_history)
            ledger: PrivateLedger | None = None
            for revision in session.private_ledger_history:
                ledger = append_revision(ledger, revision)
        except PrivacyError:
            raise PrivacyError("SESSION_RECORD_INVALID") from None
        session.vault = {item.token: item.to_domain() for item in record.vault}
        try:
            for message in session.provider_history:
                validate_tokens(message.content, session.vault)
        except PrivacyError:
            raise PrivacyError("SESSION_RECORD_INVALID") from None
        session.state = record.state.to_domain()
        session.commands = {key: item.to_domain() for key, item in record.commands.items()}
        session.terms = tuple(record.terms)
        session.literal_policies = tuple(record.literal_policies)
        known_sources = {source.id for source in session.state.sources}
        if record.processing_mode == "semantic_state" and any(
            message.role == "user" and message.source_id not in known_sources
            for message in record.provider_history
        ):
            raise PrivacyError("SESSION_RECORD_INVALID")
        session.send_marker = record.send_marker
        session.recovery_status = (
            "unknown_send" if record.send_marker is not None else "provider_unavailable"
        )
        session.private_workbench = None
        session.private_prepared = None
        session.private_prepared_version = None
        session.private_prepared_expires_at = None
        session.private_prepared_ledger_revision = None
        session.private_selected_signature = None
        session.private_ledger_candidate = None
        session.compiled_sources = ()
        self.sessions[session.id] = session
        return session

    def create_session(self, owner: str, provider: Provider | None = None) -> Session:
        if len(self.sessions) >= 100:
            raise PrivacyError("SESSION_LIMIT")
        selected = provider or self.provider
        session = Session(secrets.token_hex(16), owner, provider=selected)
        self.sessions[session.id] = session
        try:
            self._persist(session)
        except PrivacyError:
            self.sessions.pop(session.id, None)
            raise
        return session

    def select_provider(self, session: Session, provider: Provider) -> None:
        self._ensure_writable(session, allow_unknown=True)
        previous_provider = session.provider
        previous_confirmed = session.provider_confirmed
        previous_recovery = session.recovery_status
        previous_version = session.version
        self.discard(session, allow_unknown=True)
        session.provider = provider
        session.provider_confirmed = True
        if session.recovery_status == "provider_unavailable":
            session.recovery_status = "none"
        session.record("provider_selected", provider.provider_id)
        try:
            self._persist(session)
        except PrivacyError:
            session.provider = previous_provider
            session.provider_confirmed = previous_confirmed
            session.recovery_status = previous_recovery
            session.version = previous_version
            raise

    def session(self, session_id: str, owner: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None and self.store is not None:
            try:
                raw = self.store.load(owner, session_id)
            except Exception:
                raise PrivacyError("STORAGE_UNAVAILABLE") from None
            if raw is not None:
                session = self._restore(raw, owner)
        if session is None or session.closed or not secrets.compare_digest(session.owner, owner):
            raise PrivacyError("SESSION_NOT_FOUND")
        return session

    def list_sessions(self, owner: str) -> list[Session]:
        if self.store is None:
            return [
                item
                for item in self.sessions.values()
                if not item.closed and secrets.compare_digest(item.owner, owner)
            ]
        try:
            rows = self.store.list(owner)
        except Exception:
            raise PrivacyError("STORAGE_UNAVAILABLE") from None
        result: list[Session] = []
        for raw in rows:
            item = decode_record(raw)
            if secrets.compare_digest(item.owner, owner):
                cached = self.sessions.get(item.id)
                if cached is not None and not cached.closed:
                    result.append(cached)
                else:
                    result.append(self._restore(item, owner))
        return result

    def rename_session(self, session: Session, title: str) -> None:
        self._ensure_writable(session)
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        if type(title) is not str or not title.strip() or len(title) > 120:
            raise PrivacyError("INVALID_TITLE")
        try:
            title.encode("utf-8")
        except UnicodeError:
            raise PrivacyError("INVALID_TITLE") from None
        previous_title = session.title
        previous_version = session.version
        self._drop_private_preview(session)
        session.title = title.strip()
        session.version += 1
        try:
            self._persist(session)
        except PrivacyError:
            session.title = previous_title
            session.version = previous_version
            raise

    @staticmethod
    def _private_signature(provider: Provider) -> tuple[str, str, str]:
        return (provider.provider_id, provider.model, provider.serializer_version)

    def _ledger_proposer(self) -> object | None:
        if self.ledger_proposer is not None:
            return self.ledger_proposer
        if self.private_worker is None:
            return None
        try:
            from privacy_gateway.adapters.local_ledger import LocalLedgerProposer
            from privacy_gateway.adapters.local_private_worker import LocalPrivateWorker

            if isinstance(self.private_worker, LocalPrivateWorker):
                self.ledger_proposer = LocalLedgerProposer(self.private_worker)
        except (ImportError, TypeError):
            return None
        return self.ledger_proposer

    def private_ledger_available(self, session: Session | None = None) -> bool:
        proposer = self._ledger_proposer()
        if proposer is not None and callable(getattr(proposer, "propose", None)):
            return True
        if session is not None:
            return any(
                item.ledger_revision is not None and item.plan
                for item in session.private_work_history
            )
        return False

    @staticmethod
    def _ledger_revision(session: Session, revision: int) -> LedgerRevision:
        if type(revision) is not int or revision < 1:
            raise PrivacyError("LEDGER_REVISION_INVALID")
        item = next(
            (
                candidate
                for candidate in session.private_ledger_history
                if candidate.revision == revision
            ),
            None,
        )
        if item is None:
            raise PrivacyError("LEDGER_REVISION_STALE")
        ledger: PrivateLedger | None = None
        for stored in session.private_ledger_history:
            ledger = append_revision(ledger, stored)
            if stored.revision == revision:
                found = current_revision(ledger)
                if found is None:
                    raise PrivacyError("LEDGER_STATE_INVALID")
                return found
        raise PrivacyError("LEDGER_REVISION_STALE")

    @staticmethod
    def _ledger_current(session: Session) -> LedgerRevision | None:
        if not session.private_ledger_history:
            return None
        ledger: PrivateLedger | None = None
        try:
            for revision in session.private_ledger_history:
                ledger = append_revision(ledger, revision)
        except PrivacyError:
            raise PrivacyError("LEDGER_STATE_INVALID") from None
        return current_revision(ledger)

    def _drop_ledger_candidate(self, session: Session) -> None:
        session.private_ledger_candidate = None

    async def propose_private_ledger(
        self,
        session: Session,
        private_source: str,
        base_revision: int,
        request_id: str,
        *,
        structured_values: StructuredLedgerValues | None = None,
    ) -> PendingLedger | NoLedgerChanges:
        self._ensure_writable(session)
        if session.busy or session.preparing_id is not None:
            raise PrivacyError("SESSION_BUSY")
        if type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise PrivacyError("REQUEST_ID_INVALID")
        if structured_values is not None:
            private_source = structured_source(structured_values)
        if type(private_source) is not str or not private_source or len(private_source) > 4_000:
            raise PrivacyError("LEDGER_SOURCE_INVALID")
        try:
            private_source.encode("utf-8")
        except UnicodeError:
            raise PrivacyError("LEDGER_SOURCE_INVALID") from None
        proposer = self._ledger_proposer() if structured_values is None else None
        proposer_fn = getattr(proposer, "propose", None)
        if structured_values is None and not callable(proposer_fn):
            raise PrivacyError("PRIVATE_LEDGER_UNAVAILABLE")
        current = self._ledger_current(session)
        expected_base = current.revision if current is not None else 0
        if type(base_revision) is not int or base_revision != expected_base:
            self._drop_ledger_candidate(session)
            raise PrivacyError("LEDGER_REVISION_STALE")
        self._drop_ledger_candidate(session)
        if session.prepared is not None or session.private_prepared is not None:
            self.discard(session)
        else:
            self._drop_private_preview(session)
        start_version = session.version
        session.cancelled = False
        session.active_id = request_id

        async def propose() -> object:
            if structured_values is not None:
                return structured_revision(structured_values, current)
            assert callable(proposer_fn)
            return await proposer_fn(private_source, current)

        task = asyncio.create_task(propose())
        session.active_task = task
        try:
            candidate = await task
            if session.cancelled or session.closed:
                raise PrivacyError("CANCELLED")
            if session.version != start_version:
                raise PrivacyError("SESSION_CHANGED")
            if candidate is NoLedgerChanges.NO_CHANGES:
                session.record("ledger_propose", "no_changes")
                return candidate
            if type(candidate) is not LedgerRevision:
                raise PrivacyError("LEDGER_PROPOSAL_INVALID")
            if candidate.parser_version != CURRENT_PARSER_VERSION:
                raise PrivacyError("LEDGER_PROPOSAL_INVALID")
            if candidate.origin != ("user" if structured_values is not None else "model"):
                raise PrivacyError("LEDGER_PROPOSAL_INVALID")
            if candidate.source_text != private_source:
                raise PrivacyError("LEDGER_PROPOSAL_INVALID")
            candidate = validate_revision(candidate, current)
            pending = PendingLedger(
                secrets.token_hex(16),
                request_id,
                candidate,
                base_revision,
                start_version,
                time.monotonic() + 300,
            )
            session.private_ledger_candidate = pending
            session.record("ledger_propose", "ready")
            return pending
        except asyncio.CancelledError:
            raise PrivacyError("CANCELLED") from None
        finally:
            session.active_id = None
            session.active_task = None

    def confirm_private_ledger(self, session: Session, proposal_id: str) -> LedgerRevision:
        self._ensure_writable(session)
        if session.busy or session.preparing_id is not None:
            raise PrivacyError("SESSION_BUSY")
        pending = session.private_ledger_candidate
        if (
            pending is None
            or type(proposal_id) is not str
            or not secrets.compare_digest(pending.id.encode(), proposal_id.encode())
        ):
            raise PrivacyError("LEDGER_PROPOSAL_NOT_FOUND")
        current = self._ledger_current(session)
        current_revision_number = current.revision if current is not None else 0
        if (
            pending.session_version != session.version
            or pending.base_revision != current_revision_number
            or time.monotonic() >= pending.expires_at
            or pending.candidate.parent_revision != (current.revision if current else None)
            or pending.candidate.revision != current_revision_number + 1
        ):
            self._drop_ledger_candidate(session)
            raise PrivacyError("LEDGER_PROPOSAL_STALE")
        old_history = session.private_ledger_history.copy()
        old_version = session.version
        old_candidate = pending
        old_private_prepared = session.private_prepared
        old_private_workbench = session.private_workbench
        old_private_prepared_version = session.private_prepared_version
        old_private_prepared_expires_at = session.private_prepared_expires_at
        old_private_selected_signature = session.private_selected_signature
        old_private_prepared_ledger_revision = session.private_prepared_ledger_revision
        self._drop_private_preview(session)
        try:
            ledger: PrivateLedger | None = None
            for revision in old_history:
                ledger = append_revision(ledger, revision)
            ledger = append_revision(ledger, pending.candidate)
            session.private_ledger_history = list(ledger.revisions)
            session.version += 1
            session.private_ledger_candidate = None
            session.record("ledger_confirm", "committed")
            self._persist(session)
        except PrivacyError:
            session.private_ledger_history = old_history
            session.version = old_version
            session.private_ledger_candidate = old_candidate
            session.private_prepared = old_private_prepared
            session.private_workbench = old_private_workbench
            session.private_prepared_version = old_private_prepared_version
            session.private_prepared_expires_at = old_private_prepared_expires_at
            session.private_selected_signature = old_private_selected_signature
            session.private_prepared_ledger_revision = old_private_prepared_ledger_revision
            raise
        return pending.candidate

    def _drop_private_preview(self, session: Session, *, discard_work: bool = True) -> None:
        prepared = session.private_prepared
        if discard_work and prepared is not None and session.private_workbench is not None:
            session.private_workbench.discard(prepared.id)
        session.private_prepared = None
        session.private_prepared_version = None
        session.private_prepared_expires_at = None

    def _private_workbench(self, session: Session, workbench: PrivateWorkbench) -> PrivateWorkbench:
        if self.private_worker is None:
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        if workbench.worker is not self.private_worker:
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        planner = workbench.planner
        if (planner.provider_id, planner.model) != (
            session.provider.provider_id,
            session.provider.model,
        ):
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        if session.private_workbench is not workbench:
            self._drop_private_preview(session)
            session.private_workbench = workbench
        return workbench

    @staticmethod
    def _validate_public_private_boundary(session: Session, public_task: str) -> None:
        if PREFIX in public_task:
            raise PrivacyError("RESERVED_TOKEN")
        protected = tuple(
            dict.fromkeys(
                (
                    *session.terms,
                    *session.literal_policies,
                    *(b.value for b in session.vault.values() if not b.scope),
                )
                + session.state.denied_literals,
            )
        )
        if detect(public_task, protected, merge_overlaps=True):
            raise PrivacyError("OUTBOUND_BLOCKED")

    def prepare_private_work(
        self,
        session: Session,
        workbench: PrivateWorkbench,
        public_task: str,
        private_context: str,
        *,
        expected_version: int | None = None,
        expected_signature: tuple[str, str, str] | None = None,
        ledger_revision: int | None = None,
    ) -> PreparedWork:
        self._ensure_writable(session)
        if expected_version is not None and session.version != expected_version:
            raise PrivacyError("SESSION_CHANGED")
        if (
            expected_signature is not None
            and self._private_signature(session.provider) != expected_signature
        ):
            raise PrivacyError("SESSION_CHANGED")
        if not session.provider_confirmed or self.private_worker is None:
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        if session.preparing_id is not None or session.busy:
            raise PrivacyError("SESSION_BUSY")
        if ledger_revision is not None and private_context != "":
            raise PrivacyError("LEDGER_CONTEXT_CONFLICT")
        self.discard(session)
        if not isinstance(public_task, str) or not public_task.strip() or len(public_task) > 3_000:
            raise PrivacyError("PUBLIC_TASK_INVALID")
        selected_ledger: LedgerRevision | None = None
        if ledger_revision is not None:
            selected_ledger = self._ledger_revision(session, ledger_revision)
            if session.private_ledger_history[-1].revision != ledger_revision:
                raise PrivacyError("LEDGER_REVISION_STALE")
            from privacy_gateway.adapters.local_ledger import (
                PUBLIC_LEDGER_TASK_PREFIX,
                LedgerWorker,
            )

            self._validate_public_private_boundary(session, public_task)
            if len(PUBLIC_LEDGER_TASK_PREFIX) + len(public_task) > 3_000:
                raise PrivacyError("PUBLIC_TASK_INVALID")
            public_task = PUBLIC_LEDGER_TASK_PREFIX + public_task
            private_context = selected_ledger.source_text
            base = next(
                (
                    item
                    for item in session.private_ledger_history
                    if item.revision == selected_ledger.revision - 1
                ),
                None,
            )
            workbench = PrivateWorkbench(
                workbench.planner, LedgerWorker(selected_ledger, base), workbench.gate
            )
            self._drop_private_preview(session)
            session.private_workbench = workbench
        else:
            self._validate_public_private_boundary(session, public_task)
            workbench = self._private_workbench(session, workbench)
        if len(session.private_work_history) >= 100:
            raise PrivacyError("PRIVATE_WORK_HISTORY_LIMIT")
        work = workbench.prepare(public_task, private_context)
        session.latest_jev = None
        session.private_prepared = work
        session.private_prepared_version = session.version
        session.private_prepared_expires_at = time.monotonic() + 300
        session.private_selected_signature = self._private_signature(session.provider)
        session.private_prepared_ledger_revision = (
            selected_ledger.revision if selected_ledger is not None else None
        )
        session.record("private_prepare", "ready")
        self._persist(session)
        return work

    def _validate_jev_boundary(self, session: Session, body: bytes) -> None:
        pending = [strict_json(body)]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                self._validate_public_private_boundary(session, item)
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)

    def private_work_preview(self, session: Session) -> PreparedWork:
        work = session.private_prepared
        if work is None:
            raise PrivacyError("PRIVATE_WORK_NOT_FOUND")
        if (
            session.private_prepared_version != session.version
            or session.private_selected_signature != self._private_signature(session.provider)
            or session.private_prepared_expires_at is None
            or time.monotonic() >= session.private_prepared_expires_at
        ):
            self._drop_private_preview(session)
            raise PrivacyError("STALE_PREVIEW")
        planner = (
            session.private_workbench.planner if session.private_workbench is not None else None
        )
        if (
            planner is None
            or (planner.provider_id, planner.model)
            != (session.provider.provider_id, session.provider.model)
            or session.private_workbench is None
            or work.provider_signature != session.private_workbench._signature(work.stage)
            or work.gate_signature
            != (
                session.private_workbench.gate.signature
                if session.private_workbench.gate is not None
                else None
            )
        ):
            self._drop_private_preview(session)
            raise PrivacyError("STALE_PREVIEW")
        return work

    async def send_private_work(
        self, session: Session, prepared_id: str, workbench: PrivateWorkbench | None = None
    ) -> str:
        self._ensure_writable(session)
        if not session.provider_confirmed or self.private_worker is None:
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        work = self.private_work_preview(session)
        ledger_revision = session.private_prepared_ledger_revision
        if not secrets.compare_digest(work.id.encode(), prepared_id.encode()):
            raise PrivacyError("STALE_PREVIEW")
        current = session.private_workbench
        if current is None:
            if workbench is None:
                raise PrivacyError("PRIVATE_WORK_NOT_FOUND")
            current = self._private_workbench(session, workbench)
        elif workbench is not None and current is not workbench:
            raise PrivacyError("PRIVATE_WORK_APPROVAL_MISMATCH")
        if len(session.private_work_history) >= 100:
            self._drop_private_preview(session)
            raise PrivacyError("PRIVATE_WORK_HISTORY_LIMIT")
        self._validate_public_private_boundary(session, work.public_task)
        if work.stage == "jev":
            self._validate_jev_boundary(session, work.body)
        if self.store is not None:
            try:
                current_record_bytes = len(session_record(session).json_bytes())
            except Exception:
                raise PrivacyError("STORAGE_UNAVAILABLE") from None
            if current_record_bytes + PRIVATE_WORK_RECORD_RESERVE_BYTES > MAX_SESSION_RECORD_BYTES:
                self._drop_private_preview(session)
                raise PrivacyError("PRIVATE_WORK_STORAGE_LIMIT")

        self._drop_private_preview(session, discard_work=False)
        session.active_id = work.id
        session.cancelled = False
        provider = session.provider
        marker = SendMarkerRecord(
            marker_id=secrets.token_hex(16),
            prepared_id=work.id,
            request_sha256=hashlib.sha256(work.body).hexdigest(),
            provider_id=work.provider_signature[0],
            model=work.provider_signature[1],
            serializer_version=work.provider_signature[2],
            original=work.private_context,
            kind="private_work",
            public_task=work.public_task,
            private_context=work.private_context,
        )
        session.send_marker = marker
        session.recovery_status = "unknown_send"
        try:
            self._persist(session)
        except PrivacyError:
            session.active_id = None
            raise
        session.record("private_dispatch", work.provider_signature[0])
        previous_history = session.private_work_history.copy()
        previous_version = session.version

        async def consume() -> WorkResult:
            return await current.execute(work.id, work.body)

        try:
            task = asyncio.create_task(consume())
            session.active_task = task
            result = await task
            if session.cancelled or session.closed:
                raise PrivacyError("CANCELLED")
            answer = result.answer
            if len(answer) > MAX_BODY or len(answer.encode("utf-8")) > MAX_BODY:
                raise PrivacyError("RESPONSE_LIMIT")
            entry = PrivateWork(
                id=work.id,
                public_task=work.public_task,
                private_context=work.private_context,
                answer=answer,
                route=result.route,
                public_body_sha256=result.public_body_sha256,
                provider=provider.provider_id,
                model=provider.model,
                trace=result.trace,
                plan=(getattr(result, "plan", None) if ledger_revision is not None else None),
                ledger_revision=ledger_revision,
                jev=result.jev,
            )
            PrivateWorkRecord.from_domain(entry)
            session.private_work_history.append(entry)
            session.latest_jev = result.jev
            session.version += 1
            session.send_marker = None
            session.recovery_status = "none"
            session.record("private_complete", "completed")
            try:
                self._persist(session)
            except PrivacyError:
                session.private_work_history = previous_history
                session.version = previous_version
                session.send_marker = marker
                session.recovery_status = "unknown_send"
                raise
            return answer
        except JevPreviewReady as pause:
            next_work = pause.work
            try:
                if session.cancelled or session.closed:
                    raise PrivacyError("CANCELLED")
                self._validate_jev_boundary(session, next_work.body)
                session.private_prepared = next_work
                session.private_prepared_version = session.version
                session.private_prepared_expires_at = time.monotonic() + 300
                session.private_prepared_ledger_revision = ledger_revision
                session.send_marker = None
                session.recovery_status = "none"
                session.record("private_plan", "jev_approval_required")
                self._persist(session)
            except Exception:
                current.discard(next_work.id)
                self._drop_private_preview(session)
                session.send_marker = marker
                session.recovery_status = "unknown_send"
                raise
            return ""
        except JevGateBlocked as blocked:
            session.latest_jev = blocked.assessment
            session.send_marker = None
            session.recovery_status = "none"
            session.record("private_jev", blocked.assessment.decision)
            try:
                self._persist(session)
            except PrivacyError:
                session.send_marker = marker
                session.recovery_status = "unknown_send"
                raise
            raise
        except asyncio.CancelledError:
            session.record("private_complete", "cancelled")
            raise PrivacyError("CANCELLED") from None
        except ProviderNotDispatched:
            session.record("private_complete", "not_dispatched_unknown")
            raise
        except PrivacyError:
            session.record("private_complete", "failed_or_cancelled")
            raise
        except Exception:
            session.record("private_complete", "failed")
            raise PrivacyError("PRIVATE_WORK_FAILED") from None
        finally:
            session.active_id = None
            session.active_task = None
            if work.stage == "jev":
                session.latest_jev = current.latest_jev
            if session.closed:
                session.audit.clear()

    async def rerun_private_ledger(
        self, session: Session, work_id: str, ledger_revision: int
    ) -> str:
        self._ensure_writable(session)
        if session.busy or session.preparing_id is not None:
            raise PrivacyError("SESSION_BUSY")
        if type(work_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", work_id):
            raise PrivacyError("PRIVATE_WORK_NOT_FOUND")
        source = next((item for item in session.private_work_history if item.id == work_id), None)
        if source is None or source.ledger_revision is None or not isinstance(source.plan, dict):
            raise PrivacyError("LEDGER_RERUN_UNAVAILABLE")
        latest = self._ledger_current(session)
        if latest is None or ledger_revision != latest.revision:
            raise PrivacyError("LEDGER_REVISION_STALE")
        current = self._ledger_revision(session, ledger_revision)
        if (
            len(json.dumps(source.plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            > 16_000
        ):
            raise PrivacyError("PLAN_RESPONSE_INVALID")
        from privacy_gateway.adapters.local_ledger import LedgerWorker
        from privacy_gateway.application.private_work import execute_local_plan

        base = next(
            (
                item
                for item in session.private_ledger_history
                if item.revision == ledger_revision - 1
            ),
            None,
        )
        worker = LedgerWorker(current, base)
        previous_history = session.private_work_history.copy()
        previous_version = session.version
        committed = False
        appended = False
        session.cancelled = False
        session.active_id = secrets.token_hex(16)
        task = asyncio.create_task(
            execute_local_plan(
                source.plan,
                worker,
                public_task=source.public_task,
                private_context=current.source_text,
                public_body_sha256=source.public_body_sha256,
                reused=True,
            )
        )
        session.active_task = task
        try:
            result = await task
            if session.cancelled or session.closed:
                raise PrivacyError("CANCELLED")
            if session.version != previous_version:
                raise PrivacyError("SESSION_CHANGED")
            if len(result.answer) > MAX_BODY or len(result.answer.encode("utf-8")) > MAX_BODY:
                raise PrivacyError("RESPONSE_LIMIT")
            entry = PrivateWork(
                id=secrets.token_hex(16),
                public_task=source.public_task,
                private_context=current.source_text,
                answer=result.answer,
                route=result.route,
                public_body_sha256=result.public_body_sha256,
                provider=source.provider,
                model=source.model,
                trace=result.trace,
                plan=dict(source.plan),
                ledger_revision=ledger_revision,
            )
            PrivateWorkRecord.from_domain(entry)
            if len(session.private_work_history) >= 100:
                raise PrivacyError("PRIVATE_WORK_HISTORY_LIMIT")
            session.private_work_history.append(entry)
            appended = True
            session.version += 1
            session.record("ledger_rerun", "completed")
            self._persist(session)
            committed = True
            return entry.answer
        except asyncio.CancelledError:
            raise PrivacyError("CANCELLED") from None
        except PrivacyError:
            raise
        except Exception:
            raise PrivacyError("PRIVATE_WORK_FAILED") from None
        finally:
            if not committed and not session.closed and appended:
                session.private_work_history = previous_history
                session.version = previous_version
            session.active_id = None
            session.active_task = None
            if session.closed:
                session.audit.clear()

    def discard(
        self, session: Session, prepared_id: str | None = None, *, allow_unknown: bool = False
    ) -> None:
        self._ensure_writable(session, allow_unknown=allow_unknown)
        normal_match = session.prepared is not None and (
            prepared_id is None or session.prepared.id == prepared_id
        )
        private_match = session.private_prepared is not None and (
            prepared_id is None or session.private_prepared.id == prepared_id
        )
        if prepared_id is not None and not normal_match and not private_match:
            return
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        if normal_match:
            session.prepared = None
        if private_match:
            self._drop_private_preview(session)
        session.version += 1
        self._persist(session)

    def _sanitize(
        self,
        text: str,
        terms: tuple[str, ...],
        vault: dict[str, Binding],
        *,
        history: bool,
        protections: tuple[Protection, ...] = (),
        semantic: bool = False,
    ) -> tuple[str, tuple[Detection, ...]]:
        if history:
            validate_tokens(text, vault)
        elif PREFIX in text:
            raise PrivacyError("RESERVED_TOKEN")
        effective_terms = tuple(dict.fromkeys((*terms, *(b.value for b in vault.values()))))
        detections = detect(
            text, effective_terms, history=history, protections=protections, merge_overlaps=semantic
        )
        denied = {b.value for b in vault.values() if not b.restore_allowed}
        denied.update(p.quote for p in protections if not p.restore_allowed)
        denied.update(text[d.start : d.end] for d in detections if not d.restore_allowed)
        segments = TOKEN.split(text) if history else (text,)
        denied.update(
            segment[item.start : item.end]
            for segment in segments
            for item in credential_detections(segment)
        )
        for token, old in tuple(vault.items()):
            if any(value in old.value for value in denied) and old.restore_allowed:
                vault[token] = Binding(token, old.value, False, old.scope)
        known_values = {binding.value for binding in vault.values()}
        for value in sorted(denied - known_values):
            if len(vault) >= 1024:
                raise PrivacyError("VAULT_LIMIT")
            token = f"[[PG_v1_{secrets.token_hex(16)}]]"
            while token in vault:
                token = f"[[PG_v1_{secrets.token_hex(16)}]]"
            vault[token] = Binding(token, value, False)
        index = {(binding.value, binding.restore_allowed): binding for binding in vault.values()}
        replacements = []
        effective_detections = []
        for item in detections:
            value = text[item.start : item.end]
            restore_allowed = item.restore_allowed and not any(secret in value for secret in denied)
            effective_detections.append(
                Detection(item.start, item.end, item.category, restore_allowed)
            )
            binding = index.get((value, restore_allowed))
            if binding is None:
                if len(vault) >= 1024:
                    raise PrivacyError("VAULT_LIMIT")
                token = f"[[PG_v1_{secrets.token_hex(16)}]]"
                while token in vault:
                    token = f"[[PG_v1_{secrets.token_hex(16)}]]"
                binding = Binding(token, value, restore_allowed)
                vault[token] = binding
                index[(value, restore_allowed)] = binding
            replacements.append(
                Replacement(SourceSpan("current", 1, item.start, item.end), binding.token)
            )
        return apply_spans(
            text,
            replacements,
            message_id="current",
            message_revision=1,
            allow_existing_tokens=history,
        ), tuple(effective_detections)

    def validate_input(self, session: Session, text: str, terms: tuple[str, ...]) -> None:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        if not text.strip() or len(text) > 20_000:
            raise PrivacyError("INVALID_INPUT")
        try:
            text.encode("utf-8")
            for term in terms:
                term.encode("utf-8")
        except UnicodeError:
            raise PrivacyError("INVALID_INPUT") from None
        if (
            max(len(session.provider_history), len(session.private_history)) + 2
            > MAX_HISTORY_MESSAGES
        ):
            raise PrivacyError("HISTORY_LIMIT")
        if len(terms) > 30 or any(
            not term.strip() or len(term) > 200 or PREFIX in term for term in terms
        ):
            raise PrivacyError("INVALID_TERMS")
        if PREFIX in text:
            raise PrivacyError("RESERVED_TOKEN")

    async def prepare_message(
        self,
        session: Session,
        text: str,
        terms: tuple[str, ...],
        request_id: str | None = None,
        *,
        draft_identity: DraftIdentity | None = None,
        draft_facts: DraftFacts | None = None,
    ) -> Prepared:
        self._ensure_writable(session)
        if not session.provider_confirmed:
            raise PrivacyError("PROVIDER_UNAVAILABLE")
        if session.processing_mode == "semantic_state" and self.resolver is None:
            raise PrivacyError("PROCESSING_MODE_UNAVAILABLE")
        if (
            session.processing_mode == "semantic"
            and self.detector is None
            and self.resolver is None
        ):
            raise PrivacyError("PROCESSING_MODE_UNAVAILABLE")
        if (draft_identity is not None or draft_facts is not None) and self.resolver is None:
            self.discard(session)
            raise PrivacyError("STATE_DISABLED")
        if self.detector is None and self.resolver is None:
            return self.prepare(session, text, terms)
        if request_id in session.cancelled_prepares:
            raise PrivacyError("CANCELLED")
        self.discard(session)
        self.validate_input(session, text, terms)
        version = session.version
        attempt = request_id or secrets.token_hex(16)
        session.preparing_id = attempt
        sources = tuple(m.content for m in session.private_history if m.role == "user") + (text,)
        session.record("semantic", "started")
        resolution: Resolution | None = None
        try:
            if self.resolver is not None:
                literals = tuple(dict.fromkeys((*session.literal_policies, *terms)))
                if draft_facts is not None:
                    resolution = await self.resolver.resolve(
                        session.state,
                        text,
                        literals,
                        draft_identity=draft_identity,
                        draft_facts=draft_facts,
                    )
                elif draft_identity is None:
                    resolution = await self.resolver.resolve(session.state, text, literals)
                else:
                    resolution = await self.resolver.resolve(
                        session.state, text, literals, draft_identity=draft_identity
                    )
                report = resolution.report
            else:
                assert self.detector is not None
                protected = tuple(
                    dict.fromkeys((*terms, *(b.value for b in session.vault.values())))
                )
                report = await self.detector.analyze(sources, protected)
        except PrivacyError as error:
            if not session.closed:
                session.record("semantic", str(error))
            raise
        finally:
            if session.preparing_id == attempt:
                session.preparing_id = None
        if (
            session.closed
            or self.sessions.get(session.id) is not session
            or session.version != version
        ):
            raise PrivacyError("STALE_PREVIEW")
        if attempt in session.cancelled_prepares:
            raise PrivacyError("CANCELLED")
        self.validate_input(session, text, terms)
        if resolution is not None:
            if draft_facts is not None:
                verify_draft_facts(
                    session.state,
                    resolution.staged_state,
                    resolution.current_source,
                    draft_facts,
                )
            if draft_identity is not None:
                verify_draft_identity(
                    session.state,
                    resolution.staged_state,
                    resolution.current_source,
                    draft_identity,
                )
            return self.prepare_stateful(
                session,
                text,
                terms,
                resolution,
                attempt,
                draft_identity=draft_identity,
                draft_facts=draft_facts,
            )
        return self.prepare(session, text, terms, report=report, request_id=attempt)

    def correct_fact(
        self,
        session: Session,
        fact_id: str,
        revision: int,
        epoch: int,
        text: str,
        operation_id: str | None = None,
    ) -> None:
        self._ensure_writable(session)
        if self.resolver is None:
            raise PrivacyError("STATE_DISABLED")
        signature = hashlib.sha256(
            repr(("correct", fact_id, revision, epoch, text)).encode()
        ).hexdigest()
        if operation_id is not None and operation_id in session.commands:
            if session.commands[operation_id].signature != signature:
                self.discard(session)
                raise PrivacyError("STATE_COMMAND_CONFLICT")
            return
        if len(session.commands) >= 128:
            self.discard(session)
            raise PrivacyError("STATE_COMMAND_LIMIT")
        self.discard(session)
        previous_state = session.state
        previous_commands = session.commands.copy()
        previous_version = session.version
        previous_mode = session.processing_mode
        if session.state.epoch != epoch or type(epoch) is not int:
            raise PrivacyError("STATE_STALE")
        state, source = session.state.add_source(text)
        state = state.correct(fact_id, revision, state.epoch, unique_span(source, text))
        state.validate()
        session.state = state
        if operation_id is not None:
            session.commands[operation_id] = CommandReceipt(signature, state.epoch)
        session.record("fact_corrected", "committed_locally")
        session.processing_mode = "semantic_state"
        try:
            self._persist(session)
        except PrivacyError:
            session.state = previous_state
            session.commands = previous_commands
            session.version = previous_version
            session.processing_mode = previous_mode
            raise

    def identity_command(
        self, session: Session, command: IdentityCommand, operation_id: str
    ) -> CommandReceipt:
        self._ensure_writable(session)
        if self.resolver is None:
            raise PrivacyError("STATE_DISABLED")
        try:
            command = validate_identity_command(command)
        except PrivacyError:
            self.discard(session)
            raise
        signature = hashlib.sha256(command.model_dump_json().encode("utf-8")).hexdigest()
        previous = session.commands.get(operation_id)
        if previous is not None:
            if previous.signature != signature:
                self.discard(session)
                raise PrivacyError("STATE_COMMAND_CONFLICT")
            return previous
        if len(session.commands) >= 128:
            self.discard(session)
            raise PrivacyError("STATE_COMMAND_LIMIT")
        self.discard(session)
        previous_state = session.state
        previous_commands = session.commands.copy()
        previous_version = session.version
        previous_mode = session.processing_mode
        result = apply_identity(session.state, command)
        result.state.validate()
        receipt = CommandReceipt(
            signature, result.state.epoch, result.entity_id, result.policy_fact_id
        )
        session.state = result.state
        session.commands[operation_id] = receipt
        session.record("identity_" + command.kind, "committed_locally")
        session.processing_mode = "semantic_state"
        try:
            self._persist(session)
        except PrivacyError:
            session.state = previous_state
            session.commands = previous_commands
            session.version = previous_version
            session.processing_mode = previous_mode
            raise
        return receipt

    def create_fact(
        self, session: Session, command: CreateFact, operation_id: str
    ) -> CommandReceipt:
        self._ensure_writable(session)
        if self.resolver is None:
            raise PrivacyError("STATE_DISABLED")
        try:
            command = validate_manual_fact(command)
        except PrivacyError:
            self.discard(session)
            raise
        signature = hashlib.sha256(command.model_dump_json().encode("utf-8")).hexdigest()
        previous = session.commands.get(operation_id)
        if previous is not None:
            if previous.signature != signature:
                self.discard(session)
                raise PrivacyError("STATE_COMMAND_CONFLICT")
            return previous
        if len(session.commands) >= 128:
            self.discard(session)
            raise PrivacyError("STATE_COMMAND_LIMIT")
        self.discard(session)
        previous_state = session.state
        previous_commands = session.commands.copy()
        previous_version = session.version
        previous_mode = session.processing_mode
        result = apply_manual_fact(session.state, command)
        receipt = CommandReceipt(
            signature,
            result.state.epoch,
            fact_id=result.fact.id,
            fact_revision=result.fact.revision,
        )
        session.state = result.state
        session.commands[operation_id] = receipt
        session.record("fact_created", "committed_locally")
        session.processing_mode = "semantic_state"
        try:
            self._persist(session)
        except PrivacyError:
            session.state = previous_state
            session.commands = previous_commands
            session.version = previous_version
            session.processing_mode = previous_mode
            raise
        return receipt

    def prepare_stateful(
        self,
        session: Session,
        text: str,
        terms: tuple[str, ...],
        resolution: Resolution,
        request_id: str,
        *,
        draft_identity: DraftIdentity | None = None,
        draft_facts: DraftFacts | None = None,
        review_plan: ReviewPlan | None = None,
    ) -> Prepared:
        self.discard(session)
        self.validate_input(session, text, terms)
        state = resolution.delta.apply(session.state)
        if state != resolution.staged_state:
            raise PrivacyError("STATE_STALE")
        state.validate()
        current = resolution.current_source
        if current.text != text or state.source(current.id) != current:
            raise PrivacyError("STATE_SOURCE_MISMATCH")
        if not (
            set(session.state.sources) <= set(state.sources)
            and set(session.state.facts) <= set(state.facts)
            and set(session.state.entities) <= set(state.entities)
            and set(session.state.links) <= set(state.links)
            and set(session.state.rule_denials) <= set(state.rule_denials)
        ):
            raise PrivacyError("STATE_OBLIGATION_REMOVED")
        literals = tuple(dict.fromkeys((*session.literal_policies, *terms)))
        staged = session.vault.copy()
        local_view = {token: Binding(token, b.value, True) for token, b in staged.items()}
        assistant_views = {
            index: restore_text(message.content, local_view, max_bytes=MAX_BODY)
            for index, message in enumerate(session.provider_history)
            if message.role == "assistant"
        }
        state = state.protect_credentials(tuple(assistant_views.values()))
        if review_plan is None and resolution.trusted_state is not None:
            review_plan = ReviewPlan(
                session.state,
                resolution.trusted_state,
                state,
                current.id,
                draft_identity,
                draft_facts,
            )
        if (
            review_plan is not None
            and reviewed_state(review_plan, session.state, literals) != state
        ):
            raise PrivacyError("PREVIEW_DECISION_INVALID")
        for token, binding in tuple(staged.items()):
            if binding.restore_allowed and any(
                value in binding.value for value in state.denied_literals
            ):
                staged[token] = Binding(token, binding.value, False, binding.scope)
        compilation = compile_state(
            state,
            literals,
            staged,
            session.compiled_sources,
            resolver_signature=resolution.report.model + ":scoped-v1",
        )
        compiled = {item.stamp.source_id: item for item in compilation.sources}
        messages: list[Message] = []
        assistant_terms = tuple(
            dict.fromkeys((*literals, *(state.quote(f.assertion) for f in state.facts)))
        )
        for index, message in enumerate(session.provider_history):
            if message.role == "user":
                if message.source_id not in compiled:
                    raise PrivacyError("STATE_SOURCE_MISSING")
                content = compiled[message.source_id].text
            else:
                local = assistant_views[index]
                protections = tuple(
                    Protection(secret, "credential", 0, 0, secret)
                    for secret in state.denied_literals
                )
                content, _ = self._sanitize(
                    local,
                    assistant_terms,
                    staged,
                    history=True,
                    protections=protections,
                    semantic=True,
                )
            messages.append(Message(message.role, content, source_id=message.source_id))
        last = compiled[current.id]
        messages.append(Message("user", last.text, source_id=current.id))
        body = serialize_request(
            session.provider, [{"role": m.role, "content": m.content} for m in messages]
        )
        if len(body) > MAX_BODY:
            raise PrivacyError("CONTEXT_LIMIT")
        allowed = set().union(*(tokens_in(m.content) for m in messages))
        prepared = Prepared(
            secrets.token_hex(16),
            session.version,
            body,
            text,
            last.text,
            last.detections,
            MappingProxyType({token: staged[token] for token in allowed}),
            MappingProxyType(staged.copy()),
            time.monotonic() + 300,
            (
                session.provider.provider_id,
                session.provider.model,
                session.provider.serializer_version,
            ),
            resolution.report,
            request_id,
            state,
            current.id,
            literals,
            compilation,
            tuple(messages),
            resolution.model_calls,
            resolution.full_fallback,
            review_plan,
        )
        session.terms = terms
        session.processing_mode = "semantic_state"
        session.prepared = prepared
        session.record("prepare", "ready")
        self._persist(session)
        return prepared

    def review_preview(
        self,
        session: Session,
        prepared_id: str,
        operation_id: str,
        expected_epoch: int,
        command: RejectNewMask,
    ) -> tuple[CommandReceipt, Prepared | None]:
        self._ensure_writable(session)
        from privacy_gateway.adapters.state_resolver import Resolution
        from privacy_gateway.domain.state_proposals import StateDelta

        try:
            command = validate_review_command(command)
        except PrivacyError:
            self.discard(session, prepared_id)
            raise
        signature = hashlib.sha256(
            repr(("preview", prepared_id, expected_epoch, command.model_dump_json())).encode()
        ).hexdigest()
        previous = session.commands.get(operation_id)
        if previous is not None:
            if previous.signature != signature:
                self.discard(session, prepared_id)
                raise PrivacyError("STATE_COMMAND_CONFLICT")
            child = session.prepared
            available = (
                child is not None
                and child.id == previous.new_prepared_id
                and child.version == session.version
                and time.monotonic() < child.expires_at
            )
            return previous, child if available else None
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        old = session.prepared
        if old is None or old.id != prepared_id:
            raise PrivacyError("STALE_PREVIEW")
        self.discard(session)
        if (
            type(expected_epoch) is not int
            or expected_epoch != session.state.epoch
            or time.monotonic() >= old.expires_at
            or old.provider_signature
            != (
                session.provider.provider_id,
                session.provider.model,
                session.provider.serializer_version,
            )
            or old.version != session.version - 1
        ):
            raise PrivacyError("STALE_PREVIEW")
        if len(session.commands) >= 128:
            raise PrivacyError("STATE_COMMAND_LIMIT")
        if old.review_plan is None or old.semantic is None:
            raise PrivacyError("PREVIEW_DECISION_LOCKED")
        plan, state = reject_new_mask(old.review_plan, session.state, old.literal_policies, command)
        current = state.source(old.source_id)
        resolution = Resolution(
            state,
            current,
            old.semantic,
            tuple(s.id for s in state.sources),
            old.resolver_full_fallback,
            0,
            StateDelta(
                session.state,
                state,
                tuple(f for f in state.facts if f not in session.state.facts),
                tuple(link for link in state.links if link not in session.state.links),
            ),
            (),
        )
        child = self.prepare_stateful(
            session,
            old.original,
            session.terms,
            resolution,
            old.request_id,
            review_plan=plan,
        )
        child = replace(child, expires_at=min(child.expires_at, old.expires_at))
        session.prepared = child
        receipt = CommandReceipt(
            signature,
            session.state.epoch,
            old_prepared_id=old.id,
            new_prepared_id=child.id,
            body_sha256=hashlib.sha256(child.body).hexdigest(),
        )
        session.commands[operation_id] = receipt
        session.record("preview_reviewed", "new_approval_required")
        self._persist(session)
        return receipt, child

    def cancel_prepare(self, session: Session, request_id: str) -> None:
        self._ensure_writable(session, allow_unknown=True)
        if len(session.cancelled_prepares) >= 128 and request_id not in session.cancelled_prepares:
            raise PrivacyError("SESSION_LIMIT")
        session.cancelled_prepares.add(request_id)
        if session.preparing_id == request_id or (
            session.prepared is not None and session.prepared.request_id == request_id
        ):
            self.discard(session)
            session.preparing_id = None

    def prepare(
        self,
        session: Session,
        text: str,
        terms: tuple[str, ...],
        *,
        report: SemanticReport | None = None,
        request_id: str = "",
    ) -> Prepared:
        self._ensure_writable(session)
        if not session.provider_confirmed:
            raise PrivacyError("PROVIDER_UNAVAILABLE")
        if session.processing_mode == "semantic_state" and self.resolver is None:
            raise PrivacyError("PROCESSING_MODE_UNAVAILABLE")
        if (
            session.processing_mode == "semantic"
            and self.detector is None
            and self.resolver is None
            and report is None
        ):
            raise PrivacyError("PROCESSING_MODE_UNAVAILABLE")
        self.discard(session)
        self.validate_input(session, text, terms)
        terms = tuple(dict.fromkeys(terms))
        staged = session.vault.copy()
        protections = report.protections if report else ()
        constituents = [(p.quote, p.restore_allowed) for p in protections]
        if report is not None:
            constituents.extend((term, True) for term in terms)
        for quote, restore in constituents:
            if not any(b.value == quote for b in staged.values()):
                if len(staged) >= 1024:
                    raise PrivacyError("VAULT_LIMIT")
                token = f"[[PG_v1_{secrets.token_hex(16)}]]"
                staged[token] = Binding(token, quote, restore)
        messages: list[Message] = []
        for message in session.provider_history:
            content = message.content
            if report is not None:
                local_view = {t: Binding(t, b.value, True) for t, b in staged.items()}
                content = restore_text(content, local_view, max_bytes=MAX_BODY)
            sanitized, _ = self._sanitize(
                content,
                terms,
                staged,
                history=True,
                protections=protections,
                semantic=report is not None,
            )
            messages.append(Message(message.role, sanitized))
        sanitized, detections = self._sanitize(
            text, terms, staged, history=False, protections=protections, semantic=report is not None
        )
        messages.append(Message("user", sanitized))
        body = serialize_request(
            session.provider, [{"role": m.role, "content": m.content} for m in messages]
        )
        if len(body) > MAX_BODY:
            raise PrivacyError("CONTEXT_LIMIT")
        allowed = set().union(*(tokens_in(m.content) for m in messages))
        prepared = Prepared(
            secrets.token_hex(16),
            session.version,
            body,
            text,
            sanitized,
            detections,
            MappingProxyType({t: staged[t] for t in allowed}),
            MappingProxyType(staged.copy()),
            time.monotonic() + 300,
            (
                session.provider.provider_id,
                session.provider.model,
                session.provider.serializer_version,
            ),
            report,
            request_id,
            expected_messages=tuple(messages),
        )
        if report is not None:
            session.processing_mode = "semantic"
        session.terms = terms
        session.prepared = prepared
        session.record("prepare", "ready")
        self._persist(session)
        return prepared

    def gate(self, prepared: Prepared, session: Session) -> list[Message]:
        if prepared.provider_signature != (
            session.provider.provider_id,
            session.provider.model,
            session.provider.serializer_version,
        ):
            raise PrivacyError("STALE_PREVIEW")
        if prepared.version != session.version or time.monotonic() >= prepared.expires_at:
            raise PrivacyError("STALE_PREVIEW")
        if len(prepared.body) > MAX_BODY:
            raise PrivacyError("INVALID_PAYLOAD")
        payload = strict_json(prepared.body)
        if not isinstance(payload, dict) or payload.get("model") != session.provider.model:
            raise PrivacyError("INVALID_PAYLOAD")
        if not isinstance(payload.get("messages"), list) or not payload["messages"]:
            raise PrivacyError("INVALID_PAYLOAD")
        if len(payload["messages"]) > MAX_HISTORY_MESSAGES:
            raise PrivacyError("INVALID_PAYLOAD")
        expected_payload = json.loads(serialize_request(session.provider, payload["messages"]))
        if json.dumps(payload, sort_keys=True) != json.dumps(expected_payload, sort_keys=True):
            raise PrivacyError("INVALID_PAYLOAD")
        scoped = prepared.staged_state is not None
        expected = prepared.expected_messages
        if len(expected) != len(session.provider_history) + 1:
            raise PrivacyError("OUTBOUND_BLOCKED")
        if prepared.staged_state is not None:
            state = prepared.staged_state
            state.validate()
            if (
                prepared.review_plan is not None
                and reviewed_state(prepared.review_plan, session.state, prepared.literal_policies)
                != state
            ):
                raise PrivacyError("OUTBOUND_BLOCKED")
            reference = compile_state(
                state,
                prepared.literal_policies,
                dict(prepared.staged_vault),
                resolver_signature="gate-full-reference",
                full=True,
            )
            compiled = {item.stamp.source_id: item.text for item in reference.sources}
            expected = tuple(
                Message(
                    m.role,
                    compiled[m.source_id] if m.role == "user" else m.content,
                    source_id=m.source_id,
                )
                for m in prepared.expected_messages
            )
        messages = []
        for message in payload["messages"]:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not isinstance(message["role"], str)
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise PrivacyError("INVALID_PAYLOAD")
            content = message["content"]
            validate_tokens(content, prepared.manifest)
            literal_terms = prepared.literal_policies if scoped else session.terms
            if detect(content, literal_terms, history=True):
                raise PrivacyError("OUTBOUND_BLOCKED")
            segments = TOKEN.split(content)
            forbidden = (
                prepared.staged_state.denied_literals
                if prepared.staged_state is not None
                else tuple(binding.value for binding in prepared.staged_vault.values())
            )
            if any(value in segment for value in forbidden for segment in segments):
                raise PrivacyError("OUTBOUND_BLOCKED")
            messages.append(Message(message["role"], content, source_id=""))
        if payload["messages"] != [{"role": m.role, "content": m.content} for m in expected]:
            raise PrivacyError("OUTBOUND_BLOCKED")
        return [
            replace(message, source_id=expected[index].source_id)
            for index, message in enumerate(messages)
        ]

    async def send(
        self,
        session: Session,
        prepared_id: str,
        *,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        self._ensure_writable(session)
        if not session.provider_confirmed:
            raise PrivacyError("PROVIDER_UNAVAILABLE")
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        prepared = session.prepared
        if prepared is None or not secrets.compare_digest(
            prepared.id.encode(), prepared_id.encode()
        ):
            raise PrivacyError("STALE_PREVIEW")
        if on_delta is not None and not isinstance(session.provider, StreamingProvider):
            raise PrivacyError("STREAM_UNSUPPORTED")
        session.prepared = None
        messages = self.gate(prepared, session)
        session.active_id = prepared.id
        session.cancelled = False
        provider = session.provider
        marker = None
        if self.store is not None:
            marker = SendMarkerRecord(
                marker_id=secrets.token_hex(16),
                prepared_id=prepared.id,
                request_sha256=hashlib.sha256(prepared.body).hexdigest(),
                provider_id=provider.provider_id,
                model=provider.model,
                serializer_version=provider.serializer_version,
                original=prepared.original,
            )
            session.send_marker = marker
            session.recovery_status = "unknown_send"
            try:
                self._persist(session)
            except PrivacyError:
                session.active_id = None
                session.active_task = None
                raise
        session.record("dispatch", provider.provider_id)
        previous_vault = session.vault.copy()
        previous_state = session.state
        previous_private_history = session.private_history.copy()
        previous_provider_history = session.provider_history.copy()
        previous_compiled_sources = session.compiled_sources
        previous_literal_policies = session.literal_policies
        previous_version = session.version

        async def consume() -> str:
            if not isinstance(provider, StreamingProvider):
                return await provider.complete(prepared.body)
            restorer = IncrementalRestorer(prepared.manifest)
            parts: list[str] = []
            async with aclosing(provider.stream(prepared.body)) as stream:
                async for delta in stream:
                    safe = restorer.feed(delta)
                    parts.append(delta)
                    if on_delta is not None:
                        for offset in range(0, len(safe), 4096):
                            await on_delta(safe[offset : offset + 4096])
                tail = restorer.finish()
                if tail and on_delta is not None:
                    await on_delta(tail)
            return "".join(parts)

        try:
            task = asyncio.create_task(consume())
            session.active_task = task
            source = await asyncio.wait_for(task, timeout=provider.timeout)
            if session.cancelled or session.closed:
                raise PrivacyError("CANCELLED")
            if len(source) > MAX_BODY or len(source.encode("utf-8")) > MAX_BODY:
                raise PrivacyError("RESPONSE_LIMIT")
            display = restore_text(source, prepared.manifest, max_bytes=MAX_BODY)
            session.vault = dict(prepared.staged_vault)
            if prepared.staged_state is not None:
                session.state = prepared.staged_state
                session.literal_policies = prepared.literal_policies
                assert prepared.compilation is not None
                session.compiled_sources = prepared.compilation.sources
            session.provider_history = messages + [Message("assistant", source)]
            session.private_history += [
                Message("user", prepared.original, source_id=prepared.source_id),
                Message("assistant", display, provider.provider_id),
            ]
            session.version += 1
            session.record("complete", "completed")
            if self.store is not None:
                session.send_marker = None
                session.recovery_status = "none"
                try:
                    self._persist(session)
                except PrivacyError:
                    session.send_marker = marker
                    session.recovery_status = "unknown_send"
                    session.vault = previous_vault
                    session.state = previous_state
                    session.private_history = previous_private_history
                    session.provider_history = previous_provider_history
                    session.compiled_sources = previous_compiled_sources
                    session.literal_policies = previous_literal_policies
                    session.version = previous_version
                    raise
            return display
        except asyncio.CancelledError:
            session.record("complete", "cancelled")
            raise PrivacyError("CANCELLED") from None
        except ProviderNotDispatched:
            session.record("complete", "not_dispatched")
            if marker is not None and session.send_marker is marker and not session.closed:
                session.send_marker = None
                session.recovery_status = "none"
                try:
                    self._persist(session)
                except PrivacyError:
                    session.send_marker = marker
                    raise
            raise
        except PrivacyError:
            session.record("complete", "failed_or_cancelled")
            raise
        except Exception:
            session.record("complete", "failed")
            raise PrivacyError("PROVIDER_FAILED") from None
        finally:
            session.active_id = None
            session.active_task = None
            if session.closed:
                session.audit.clear()

    def cancel(self, session: Session, prepared_id: str | None = None) -> None:
        self._ensure_writable(session, allow_unknown=True)
        valid_ids = {
            session.active_id,
            session.prepared.id if session.prepared else None,
            session.private_prepared.id if session.private_prepared else None,
            session.private_ledger_candidate.id if session.private_ledger_candidate else None,
            session.private_ledger_candidate.request_id
            if session.private_ledger_candidate
            else None,
        }
        if prepared_id is not None and prepared_id not in valid_ids:
            return
        if not session.cancelled and session.active_task is not None:
            session.active_task.cancel()
        session.cancelled = True
        session.prepared = None
        self._drop_private_preview(session)
        self._drop_ledger_candidate(session)
        session.version += 1
        session.record("cancel", "requested")
        self._persist(session)

    def delete(self, session: Session) -> None:
        self.cancel(session)
        if self.store is not None:
            try:
                self.store.delete(session.owner, session.id)
            except Exception:
                session.persistence_failed = True
                session.recovery_status = "storage_failed"
                raise PrivacyError("STORAGE_UNAVAILABLE") from None
        session.closed = True
        session.private_history.clear()
        session.provider_history.clear()
        session.private_work_history.clear()
        session.private_ledger_history.clear()
        session.private_ledger_candidate = None
        session.vault.clear()
        session.state = State()
        session.compiled_sources = ()
        session.literal_policies = ()
        session.commands.clear()
        session.terms = ()
        session.audit.clear()
        self.sessions.pop(session.id, None)
