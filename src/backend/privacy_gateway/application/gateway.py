import asyncio
import secrets
from dataclasses import dataclass, field, replace

from privacy_gateway.adapters.local_censitive import LocalCensitiveMethod
from privacy_gateway.adapters.openai_provider import OpenAIProvider
from privacy_gateway.application.censitive_method import (
    MethodPreparation,
    finish_method,
    prepare_method,
)
from privacy_gateway.domain.detection import EMAIL, credential_detections
from privacy_gateway.domain.redaction import PrivacyError


@dataclass
class Prepared:
    id: str
    request_id: str
    original: str
    method: MethodPreparation
    body: bytes


@dataclass
class Session:
    id: str
    owner: str
    provider: object
    title: str = ""
    terms: tuple[str, ...] = ()
    messages: list[dict[str, object]] = field(default_factory=list)
    prepared: Prepared | None = None
    preparing_id: str = ""
    cancelled_prepares: set[str] = field(default_factory=set)
    active_id: str = ""
    active_task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self.active_task is not None or bool(self.preparing_id)


class Gateway:
    def __init__(self, provider=None, method=None) -> None:
        self.provider = provider or OpenAIProvider.from_environment()
        self.method = method or LocalCensitiveMethod()
        self.sessions: dict[str, Session] = {}

    def create_session(self, owner: str, provider=None) -> Session:
        session = Session(secrets.token_hex(16), owner, provider or self.provider)
        self.sessions[session.id] = session
        return session

    def session(self, session_id: str, owner: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None or not secrets.compare_digest(session.owner, owner):
            raise PrivacyError("SESSION_NOT_FOUND")
        return session

    def list_sessions(self, owner: str) -> list[Session]:
        return [session for session in self.sessions.values() if session.owner == owner]

    def discard(self, session: Session, prepared_id: str | None = None) -> None:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        if prepared_id is None or (session.prepared and session.prepared.id == prepared_id):
            session.prepared = None

    def select_provider(self, session: Session, provider) -> None:
        self.discard(session)
        session.provider = provider

    def rename_session(self, session: Session, title: str) -> None:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        if not title.strip():
            raise PrivacyError("INVALID_INPUT")
        session.title = title.strip()

    async def prepare_message(
        self, session: Session, text: str, terms: tuple[str, ...], request_id: str | None = None
    ) -> Prepared:
        self.discard(session)
        if not text.strip():
            raise PrivacyError("INVALID_INPUT")
        if any(message["role"] == "assistant" for message in session.messages):
            raise PrivacyError("METHOD_ONE_SHOT")
        attempt = request_id or secrets.token_hex(16)
        if attempt in session.cancelled_prepares:
            raise PrivacyError("CANCELLED")
        session.preparing_id = attempt
        try:
            await self.method.preflight()
            report = await self.method.detector.analyze((text,), terms)
            credentials = credential_detections(text)
            literals = tuple(
                dict.fromkeys(
                    [
                        *((p.quote, p.category) for p in report.protections),
                        *((match.group(), "email") for match in EMAIL.finditer(text)),
                        *((text[d.start : d.end], "credential") for d in credentials),
                        *(
                            (str(f["quote"]), str(f["category"]))
                            for f in self.method.detector.dropped_findings
                        ),
                        *((term, "manual") for term in terms),
                    ]
                )
            )
            denied = tuple(
                dict.fromkeys(
                    [
                        *(p.quote for p in report.protections if not p.restore_allowed),
                        *(text[d.start : d.end] for d in credentials),
                    ]
                )
            )
            method = await prepare_method(self.method, text, literals, denied)
            if attempt in session.cancelled_prepares:
                raise PrivacyError("CANCELLED")
            prepared = Prepared(
                secrets.token_hex(16),
                attempt,
                text,
                method,
                session.provider.body(method.sent),
            )
            session.terms = tuple(dict.fromkeys(terms))
            session.prepared = prepared
            return prepared
        finally:
            if session.preparing_id == attempt:
                session.preparing_id = ""

    def prepared(self, session: Session, prepared_id: str) -> Prepared:
        prepared = session.prepared
        if prepared is None or prepared.id != prepared_id:
            raise PrivacyError("STALE_PREVIEW")
        return prepared

    def adjust_method(
        self, session: Session, prepared_id: str, released: tuple[str, ...], masked: tuple[str, ...]
    ) -> Prepared:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        prepared = self.prepared(session, prepared_id)
        method = prepared.method.adjust(prepared.original, released, masked)
        session.prepared = replace(
            prepared,
            id=secrets.token_hex(16),
            method=method,
            body=session.provider.body(method.sent),
        )
        return session.prepared

    async def send(self, session: Session, prepared_id: str) -> str:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        prepared = self.prepared(session, prepared_id)
        if prepared.body != session.provider.body(prepared.method.sent):
            session.prepared = None
            raise PrivacyError("OUTBOUND_BLOCKED")
        session.prepared = None
        session.active_id = prepared.id

        async def complete() -> tuple[str, str, bool]:
            await self.method.preflight()
            source = await session.provider.complete(prepared.body)
            answer, flagged = await finish_method(
                self.method, prepared.original, prepared.method, source
            )
            return source, answer, flagged

        task = asyncio.create_task(complete())
        session.active_task = task
        try:
            source, answer, flagged = await task
            session.messages += [
                {"role": "user", "content": prepared.original},
                {
                    "role": "assistant",
                    "content": answer,
                    "provider": session.provider.provider_id,
                    "metadata": {
                        "route": "local_aggregation",
                        "public_plan": source,
                        "numeric_warning": flagged,
                    },
                },
            ]
            return answer
        except asyncio.CancelledError:
            raise PrivacyError("CANCELLED") from None
        except PrivacyError:
            raise
        except Exception:
            raise PrivacyError("PROVIDER_FAILED") from None
        finally:
            session.active_id = ""
            session.active_task = None

    def cancel_prepare(self, session: Session, request_id: str) -> None:
        session.cancelled_prepares.add(request_id)
        if session.prepared and session.prepared.request_id == request_id:
            session.prepared = None

    def cancel(self, session: Session, prepared_id: str) -> None:
        if session.active_id == prepared_id and session.active_task:
            session.active_task.cancel()
        if session.prepared and session.prepared.id == prepared_id:
            session.prepared = None

    def delete(self, session: Session) -> None:
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        del self.sessions[session.id]
