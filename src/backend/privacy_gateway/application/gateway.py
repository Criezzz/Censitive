import asyncio
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.adapters.local_detector import SemanticDetector
from privacy_gateway.adapters.provider import Provider
from privacy_gateway.domain.detection import Detection, detect
from privacy_gateway.domain.models import Replacement, SourceSpan
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

MAX_MESSAGES = 40
MAX_BODY = 200_000


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    provider: str = ""


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

    @property
    def busy(self) -> bool:
        return self.active_id is not None

    def record(self, event: str, status: str) -> None:
        self.audit.append({"event": event, "status": status, "version": self.version})
        self.audit[:] = self.audit[-100:]


class Gateway:
    def __init__(
        self, provider: Provider | None = None, detector: SemanticDetector | None = None
    ) -> None:
        self.sessions: dict[str, Session] = {}
        self.provider = provider or FakeProvider()
        self.detector = detector

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
        for token, old in tuple(vault.items()):
            if any(value in old.value for value in denied) and old.restore_allowed:
                vault[token] = Binding(token, old.value, False)
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
    ) -> Prepared:
        if self.detector is None:
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
        try:
            protected = tuple(dict.fromkeys((*terms, *(b.value for b in session.vault.values()))))
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
        return self.prepare(session, text, terms, report=report, request_id=attempt)

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
        body = json.dumps(
            {
                "model": session.provider.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
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
        try:
            payload = json.loads(prepared.body)
        except (ValueError, UnicodeError):
            raise PrivacyError("INVALID_PAYLOAD") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"model", "messages"}
            or payload["model"] != session.provider.model
        ):
            raise PrivacyError("INVALID_PAYLOAD")
        if not isinstance(payload["messages"], list) or not payload["messages"]:
            raise PrivacyError("INVALID_PAYLOAD")
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
            if detect(content, session.terms, history=True):
                raise PrivacyError("OUTBOUND_BLOCKED")
            segments = TOKEN.split(content)
            if any(
                binding.value in segment
                for binding in prepared.staged_vault.values()
                for segment in segments
            ):
                raise PrivacyError("OUTBOUND_BLOCKED")
            messages.append(Message(message["role"], content))
        return messages

    async def send(self, session: Session, prepared_id: str) -> str:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        prepared = session.prepared
        if prepared is None or not secrets.compare_digest(
            prepared.id.encode(), prepared_id.encode()
        ):
            raise PrivacyError("STALE_PREVIEW")
        session.prepared = None
        messages = self.gate(prepared, session)
        session.active_id = prepared.id
        session.cancelled = False
        provider = session.provider
        session.record("dispatch", provider.provider_id)
        try:
            session.active_task = asyncio.create_task(provider.complete(prepared.body))
            source = await asyncio.wait_for(session.active_task, timeout=provider.timeout)
            if session.cancelled or session.closed:
                raise PrivacyError("CANCELLED")
            if len(source) > MAX_BODY or len(source.encode("utf-8")) > MAX_BODY:
                raise PrivacyError("RESPONSE_LIMIT")
            display = restore_text(source, prepared.manifest, max_bytes=MAX_BODY)
            session.vault = dict(prepared.staged_vault)
            session.provider_history = messages + [Message("assistant", source)]
            session.private_history += [
                Message("user", prepared.original),
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
        session.terms = ()
        session.audit.clear()
        self.sessions.pop(session.id, None)
