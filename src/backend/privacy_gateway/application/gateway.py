from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.adapters.local_detector import SemanticDetector
from privacy_gateway.adapters.provider import Provider, StreamingProvider, serialize_request
from privacy_gateway.domain.detection import Detection, credential_detections, detect
from privacy_gateway.domain.identity import (
    DraftIdentity,
    IdentityCommand,
    apply_identity,
    validate_identity_command,
    verify_draft_identity,
)
from privacy_gateway.domain.incremental import Compilation, CompiledSource, compile_state
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

if TYPE_CHECKING:
    from privacy_gateway.adapters.state_resolver import Resolution, StateResolver

MAX_MESSAGES = 40
MAX_BODY = 200_000


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


@dataclass
class Session:
    id: str
    owner: str
    provider: Provider = field(default_factory=FakeProvider)
    version: int = 0
    terms: tuple[str, ...] = ()
    private_history: list[Message] = field(default_factory=list)
    provider_history: list[Message] = field(default_factory=list)
    vault: dict[str, Binding] = field(default_factory=dict)
    prepared: Prepared | None = None
    preparing_id: str | None = None
    cancelled_prepares: set[str] = field(default_factory=set)
    active_id: str | None = None
    active_task: asyncio.Task[str] | None = None
    cancelled: bool = False
    closed: bool = False
    audit: list[dict[str, str | int]] = field(default_factory=list)
    state: State = field(default_factory=State)
    compiled_sources: tuple[CompiledSource, ...] = ()
    literal_policies: tuple[str, ...] = ()
    commands: dict[str, CommandReceipt] = field(default_factory=dict)

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
    ) -> None:
        self.sessions: dict[str, Session] = {}
        self.provider = provider or FakeProvider()
        self.detector = detector
        self.resolver = resolver

    def create_session(self, owner: str) -> Session:
        if len(self.sessions) >= 100:
            raise PrivacyError("SESSION_LIMIT")
        session = Session(secrets.token_hex(16), owner, provider=self.provider)
        self.sessions[session.id] = session
        return session

    def select_provider(self, session: Session, provider: Provider) -> None:
        self.discard(session)
        session.provider = provider
        session.record("provider_selected", provider.provider_id)

    def session(self, session_id: str, owner: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None or session.closed or not secrets.compare_digest(session.owner, owner):
            raise PrivacyError("SESSION_NOT_FOUND")
        return session

    def discard(self, session: Session, prepared_id: str | None = None) -> None:
        if prepared_id is not None and (
            session.prepared is None or session.prepared.id != prepared_id
        ):
            return
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        session.prepared = None
        session.version += 1

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
        if len(session.provider_history) >= MAX_MESSAGES:
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
        if session.state.epoch != epoch or type(epoch) is not int:
            raise PrivacyError("STATE_STALE")
        state, source = session.state.add_source(text)
        state = state.correct(fact_id, revision, state.epoch, unique_span(source, text))
        state.validate()
        session.state = state
        if operation_id is not None:
            session.commands[operation_id] = CommandReceipt(signature, state.epoch)
        session.record("fact_corrected", "committed_locally")

    def identity_command(
        self, session: Session, command: IdentityCommand, operation_id: str
    ) -> CommandReceipt:
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
        result = apply_identity(session.state, command)
        result.state.validate()
        receipt = CommandReceipt(
            signature, result.state.epoch, result.entity_id, result.policy_fact_id
        )
        session.state = result.state
        session.commands[operation_id] = receipt
        session.record("identity_" + command.kind, "committed_locally")
        return receipt

    def create_fact(
        self, session: Session, command: CreateFact, operation_id: str
    ) -> CommandReceipt:
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
            raise PrivacyError("HISTORY_LIMIT")
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
        session.prepared = prepared
        session.record("prepare", "ready")
        return prepared

    def review_preview(
        self,
        session: Session,
        prepared_id: str,
        operation_id: str,
        expected_epoch: int,
        command: RejectNewMask,
    ) -> tuple[CommandReceipt, Prepared | None]:
        from privacy_gateway.adapters.state_resolver import Resolution
        from privacy_gateway.domain.state_proposals import StateDelta

        try:
            if type(command) is not RejectNewMask:
                raise ValueError("invalid command")
            command = RejectNewMask.model_validate(vars(command))
        except (ValidationError, ValueError, TypeError, AttributeError):
            self.discard(session, prepared_id)
            raise PrivacyError("PREVIEW_DECISION_INVALID") from None
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
        return receipt, child

    def cancel_prepare(self, session: Session, request_id: str) -> None:
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
            raise PrivacyError("HISTORY_LIMIT")
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
        )
        session.terms = terms
        session.prepared = prepared
        session.record("prepare", "ready")
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
        expected_payload = json.loads(serialize_request(session.provider, payload["messages"]))
        if json.dumps(payload, sort_keys=True) != json.dumps(expected_payload, sort_keys=True):
            raise PrivacyError("INVALID_PAYLOAD")
        scoped = prepared.staged_state is not None
        expected: tuple[Message, ...] = ()
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
            if payload["messages"] != [{"role": m.role, "content": m.content} for m in expected]:
                raise PrivacyError("OUTBOUND_BLOCKED")
        messages = []
        for index, message in enumerate(payload["messages"]):
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
            messages.append(
                Message(
                    message["role"], content, source_id=expected[index].source_id if scoped else ""
                )
            )
        return messages

    async def send(
        self,
        session: Session,
        prepared_id: str,
        *,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
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
        session.record("dispatch", provider.provider_id)

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
            session.active_task = asyncio.create_task(consume())
            source = await asyncio.wait_for(session.active_task, timeout=provider.timeout)
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
            return display
        except asyncio.CancelledError:
            session.record("complete", "cancelled")
            raise PrivacyError("CANCELLED") from None
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
        if prepared_id is not None and prepared_id not in {
            session.active_id,
            session.prepared.id if session.prepared else None,
        }:
            return
        if not session.cancelled and session.active_task is not None:
            session.active_task.cancel()
        session.cancelled = True
        session.prepared = None
        session.version += 1
        session.record("cancel", "requested")

    def delete(self, session: Session) -> None:
        self.cancel(session)
        session.closed = True
        session.private_history.clear()
        session.provider_history.clear()
        session.vault.clear()
        session.state = State()
        session.compiled_sources = ()
        session.literal_policies = ()
        session.commands.clear()
        session.terms = ()
        session.audit.clear()
        self.sessions.pop(session.id, None)
