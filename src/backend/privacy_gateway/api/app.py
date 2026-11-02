import asyncio
import json
import re
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from math import ceil
from pathlib import Path
from typing import Annotated

from anyio import CancelScope
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send

from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.adapters.conversation_store import (
    ConversationStoreError,
    EncryptedConversationStore,
)
from privacy_gateway.adapters.provider import StreamingProvider
from privacy_gateway.application.gateway import Gateway, Prepared, Session
from privacy_gateway.domain.identity import DraftIdentity, IdentityCommand
from privacy_gateway.domain.manual_facts import CreateFact, DraftFacts
from privacy_gateway.domain.models import SpanValidationError
from privacy_gateway.domain.preview_review import RejectNewMask, review_items
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json
from privacy_gateway.domain.state import State


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, events: AsyncGenerator[bytes, None]) -> None:
        self.events = events
        super().__init__(events, media_type="application/x-ndjson")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await self.events.aclose()


class PrepareInput(StrictInput):
    text: str = Field(min_length=1, max_length=20_000)
    terms: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=30
    )
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    draft_identity: DraftIdentity | None = None
    draft_facts: DraftFacts | None = None


class SnapshotInput(StrictInput):
    prepared_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


class CorrectionInput(StrictInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    fact_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)
    expected_epoch: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=800)


class IdentityInput(StrictInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    command: IdentityCommand


class CreateFactInput(StrictInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    command: CreateFact


class PreviewDecisionInput(StrictInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_epoch: int = Field(ge=0)
    command: RejectNewMask


def state_view(state: State, enabled: bool) -> dict[str, object]:
    return {
        "enabled": enabled,
        "epoch": state.epoch,
        "facts": [
            {
                "id": fact.id,
                "revision": fact.revision,
                "value": state.quote(fact.assertion),
                "category": fact.category,
                "scope": fact.scope,
                "superseded": fact.superseded,
                "source_id": fact.assertion.message_id,
                "source_revision": fact.assertion.message_revision,
                "start": fact.assertion.start,
                "end": fact.assertion.end,
            }
            for fact in state.facts
        ],
        "sources": [
            {
                "id": source.id,
                "revision": source.revision,
                "text": source.text,
                "digest": source.digest,
            }
            for source in state.sources
        ],
        "entities": [
            {
                "id": entity.id,
                "value": state.quote(entity.evidence),
                "source_id": entity.evidence.message_id,
                "source_revision": entity.evidence.message_revision,
                "start": entity.evidence.start,
                "end": entity.evidence.end,
                "protected": any(
                    f.entity_id == entity.id and f.scope == "entity" for f in state.facts
                ),
                "aliases": [
                    {
                        "value": state.quote(link.mention),
                        "source_id": link.mention.message_id,
                        "source_revision": link.mention.message_revision,
                        "start": link.mention.start,
                        "end": link.mention.end,
                    }
                    for link in state.links
                    if link.relation == "alias" and link.target == entity.id
                ],
            }
            for entity in state.entities
        ],
    }


class ProviderInput(StrictInput):
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)


class CreateSessionInput(StrictInput):
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    model: str | None = Field(default=None, min_length=1, max_length=100)


class TitleInput(StrictInput):
    title: str = Field(min_length=1, max_length=120)


def create_app(
    gateway: Gateway | None = None,
    port: int = 8765,
    connections: ProviderConnections | None = None,
    conversation_store: EncryptedConversationStore | None = None,
) -> FastAPI:
    service = gateway or Gateway()
    connections = connections or ProviderConnections(service.provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await connections.close()
            if conversation_store is not None:
                conversation_store.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.gateway = service
    clients: dict[str, str] = {}
    known_owners: set[str] = set()
    if conversation_store is not None:
        saved_owners = conversation_store.get("auth", "owners")
        if saved_owners is not None:
            entries = saved_owners.data.get("owners")
            if (
                set(saved_owners.data) != {"owners"}
                or not isinstance(entries, list)
                or len(entries) > 200
                or any(
                    not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                    for item in entries
                )
            ):
                raise PrivacyError("STORAGE_INVALID")
            known_owners = {item for item in entries if isinstance(item, str)}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if host not in hosts or (origin is not None and origin not in origins):
            return JSONResponse({"code": "LOCAL_ACCESS_DENIED"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"code": "LOCAL_ACCESS_DENIED"}, status_code=403)
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            owner = request.cookies.get("censitive_owner", "")
            csrf = clients.get(owner)
            if csrf is None or not secrets.compare_digest(
                csrf.encode(), request.headers.get("x-csrf-token", "").encode()
            ):
                return JSONResponse({"code": "AUTH_REQUIRED"}, status_code=403)
            prepare_path = re.fullmatch(r"/api/sessions/([0-9a-f]{32})/prepare", request.url.path)
            if request.method == "POST" and prepare_path:
                try:
                    service.discard(service.session(prepare_path[1], owner))
                except PrivacyError as error:
                    return JSONResponse({"code": str(error)}, status_code=409)
            command_path = re.fullmatch(
                r"/api/sessions/([0-9a-f]{32})/(?:identity|facts/(?:correct|create))",
                request.url.path,
            )
            review_path = re.fullmatch(
                r"/api/sessions/([0-9a-f]{32})/prepared/([0-9a-f]{32})/decisions",
                request.url.path,
            )

            def reject_body(code: str, status: int) -> JSONResponse:
                if request.method == "POST" and review_path:
                    try:
                        service.discard(service.session(review_path[1], owner), review_path[2])
                    except PrivacyError as error:
                        return JSONResponse({"code": str(error)}, status_code=409)
                if request.method == "POST" and command_path:
                    try:
                        service.discard(service.session(command_path[1], owner))
                    except PrivacyError as error:
                        return JSONResponse({"code": str(error)}, status_code=409)
                return JSONResponse({"code": code}, status_code=status)

            length = request.headers.get("content-length", "")
            if (
                not length.isascii()
                or not length.isdecimal()
                or len(length) > 6
                or int(length) > 100_000
            ):
                return reject_body("INPUT_TOO_LARGE", 413)
            if request.method == "POST" and not request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return reject_body("TEXT_JSON_ONLY", 415)
            received = bytearray()
            async for chunk in request.stream():
                received.extend(chunk)
                if len(received) > 100_000:
                    return reject_body("INPUT_TOO_LARGE", 413)
            request._body = bytes(received)
            if request.method == "POST" and review_path:
                try:
                    strict_json(request._body)
                except PrivacyError:
                    return reject_body("INVALID_INPUT", 422)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(PrivacyError)
    async def privacy_error(request: Request, error: PrivacyError) -> JSONResponse:
        session_id = request.path_params.get("session_id")
        if isinstance(session_id, str):
            try:
                session = service.session(session_id, owner(request))
            except PrivacyError:
                pass
            else:
                session.record("reject", str(error))
        return JSONResponse({"code": str(error)}, status_code=409)

    @app.exception_handler(ConversationStoreError)
    async def storage_error(_: Request, error: ConversationStoreError) -> JSONResponse:
        return JSONResponse({"code": "STORAGE_UNAVAILABLE"}, status_code=503)

    @app.exception_handler(SpanValidationError)
    async def span_error(_: Request, error: SpanValidationError) -> JSONResponse:
        return JSONResponse({"code": error.code.value}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        command_path = re.fullmatch(
            r"/api/sessions/([0-9a-f]{32})/(?:identity|facts/(?:correct|create))", request.url.path
        )
        review_path = re.fullmatch(
            r"/api/sessions/([0-9a-f]{32})/prepared/([0-9a-f]{32})/decisions", request.url.path
        )
        if request.method == "POST" and review_path:
            try:
                service.discard(service.session(review_path[1], owner(request)), review_path[2])
            except PrivacyError as error:
                return JSONResponse({"code": str(error)}, status_code=409)
        if request.method == "POST" and command_path:
            try:
                service.discard(service.session(command_path[1], owner(request)))
            except PrivacyError as error:
                return JSONResponse({"code": str(error)}, status_code=409)
        return JSONResponse({"code": "INVALID_INPUT"}, status_code=422)

    def owner(request: Request) -> str:
        value = request.cookies.get("censitive_owner", "")
        if value not in clients:
            raise PrivacyError("AUTH_REQUIRED")
        return value

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> JSONResponse:
        value = request.cookies.get("censitive_owner", "")
        if value not in clients:
            if value not in known_owners:
                if len(clients) >= 200 or len(known_owners) >= 200:
                    raise PrivacyError("CLIENT_LIMIT")
                value = secrets.token_hex(32)
                if conversation_store is not None:
                    conversation_store.put(
                        "auth",
                        "owners",
                        {"owners": [item for item in sorted(known_owners | {value})]},
                    )
                    known_owners.add(value)
            clients[value] = secrets.token_hex(32)
        response = JSONResponse(
            {
                "csrf": clients[value],
                "provider": service.provider.provider_id,
                "model": service.provider.model,
                "mode": "semantic-state"
                if service.resolver
                else ("rules-manual-semantic" if service.detector else "rules-and-manual"),
                "detector": service.resolver.model
                if service.resolver
                else (service.detector.model if service.detector else "rules"),
                "state_enabled": service.resolver is not None,
                "persistence_enabled": conversation_store is not None,
            }
        )
        response.set_cookie(
            "censitive_owner",
            value,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=365 * 24 * 60 * 60 if conversation_store is not None else None,
        )
        return response

    @app.post("/api/sessions")
    async def create_session(data: CreateSessionInput, request: Request) -> dict[str, str]:
        client_owner = owner(request)
        selected = service.provider
        if data.provider is not None or data.model is not None:
            selected = await connections.resolve(data.provider or "", data.model or "")
        session = service.create_session(client_owner, selected)
        return {"id": session.id, "provider": selected.provider_id, "model": selected.model}

    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> dict[str, object]:
        return {
            "sessions": [
                {
                    "id": session.id,
                    "title": session.title,
                    "provider": session.provider.provider_id,
                    "model": session.provider.model,
                    "message_count": len(session.private_history),
                    "provider_confirmed": session.provider_confirmed,
                    "recovery_status": session.recovery_status,
                }
                for session in service.list_sessions(owner(request))
            ]
        }

    @app.post("/api/sessions/{session_id}/title")
    async def rename_session(session_id: str, data: TitleInput, request: Request) -> dict[str, str]:
        session = service.session(session_id, owner(request))
        service.rename_session(session, data.title)
        return {"id": session.id, "title": session.title}

    @app.get("/api/providers")
    async def providers(request: Request) -> dict[str, object]:
        owner(request)
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/check")
    async def check_connection(data: StrictInput, request: Request) -> dict[str, object]:
        owner(request)
        await connections.check()
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/login")
    async def login(data: StrictInput, request: Request) -> dict[str, object]:
        await connections.start_login(owner(request))
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/login/cancel")
    async def cancel_login(data: StrictInput, request: Request) -> dict[str, object]:
        await connections.cancel_login(owner(request))
        return {"providers": connections.catalog()}

    @app.post("/api/sessions/{session_id}/provider")
    async def select_provider(
        session_id: str,
        data: ProviderInput,
        request: Request,
    ) -> dict[str, str]:
        session = service.session(session_id, owner(request))
        if session.busy:
            raise PrivacyError("SESSION_BUSY")
        version = session.version
        selected = await connections.resolve(data.provider, data.model)
        session = service.session(session_id, owner(request))
        if session.version != version:
            raise PrivacyError("SESSION_CHANGED")
        service.select_provider(session, selected)
        return {"id": session.id, "provider": selected.provider_id, "model": selected.model}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        return {
            "id": session.id,
            "title": session.title,
            "provider_confirmed": session.provider_confirmed,
            "recovery_status": session.recovery_status,
            "interrupted_turn": (
                {"text": session.send_marker.original, "status": "unknown"}
                if session.send_marker is not None
                else None
            ),
            "provider": session.provider.provider_id,
            "model": session.provider.model,
            "messages": [
                {"role": m.role, "content": m.content, "provider": m.provider}
                for m in session.private_history
            ],
            "terms": session.terms,
            "busy": session.busy,
            "audit": session.audit,
            "state": state_view(session.state, service.resolver is not None),
        }

    @app.post("/api/sessions/{session_id}/identity")
    async def identity_command(
        session_id: str, data: IdentityInput, request: Request
    ) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        receipt = service.identity_command(session, data.command, data.operation_id)
        return {
            "receipt": {
                "operation_id": data.operation_id,
                "committed_epoch": receipt.committed_epoch,
                "entity_id": receipt.entity_id,
                "policy_fact_id": receipt.policy_fact_id,
            },
            "state": state_view(session.state, True),
        }

    @app.post("/api/sessions/{session_id}/facts/create")
    async def create_fact(
        session_id: str, data: CreateFactInput, request: Request
    ) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        receipt = service.create_fact(session, data.command, data.operation_id)
        return {
            "receipt": {
                "operation_id": data.operation_id,
                "committed_epoch": receipt.committed_epoch,
                "fact_id": receipt.fact_id,
                "fact_revision": receipt.fact_revision,
            },
            "state": state_view(session.state, True),
        }

    @app.post("/api/sessions/{session_id}/facts/correct")
    async def correct_fact(
        session_id: str, data: CorrectionInput, request: Request
    ) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        service.correct_fact(
            session,
            data.fact_id,
            data.expected_revision,
            data.expected_epoch,
            data.text,
            data.operation_id,
        )
        return state_view(session.state, True)

    def preview_view(prepared: Prepared, session: Session) -> dict[str, object]:
        return {
            "id": prepared.id,
            "sanitized": prepared.sanitized,
            "body": prepared.body.decode("utf-8"),
            "expires_in": max(0, ceil(prepared.expires_at - time.monotonic())),
            "detections": [
                {
                    "start": d.start,
                    "end": d.end,
                    "category": d.category,
                    "value": prepared.original[d.start : d.end],
                    "restore_allowed": d.restore_allowed,
                }
                for d in prepared.detections
            ],
            "history_count": len(session.provider_history),
            "streaming": isinstance(session.provider, StreamingProvider),
            "review": None
            if prepared.review_plan is None
            else {
                "expected_epoch": prepared.review_plan.base.epoch,
                "items": review_items(prepared.review_plan, prepared.literal_policies),
            },
            "state": state_view(prepared.staged_state, True)
            if prepared.staged_state is not None
            else None,
            "work": None
            if prepared.compilation is None
            else {
                "recomputed_sources": prepared.compilation.recomputed,
                "reused_sources": prepared.compilation.reused,
                "compilation_full_fallback": prepared.compilation.full_fallback,
                "model_calls": prepared.resolver_calls,
                "resolver_full_fallback": prepared.resolver_full_fallback,
            },
            "semantic": None
            if prepared.semantic is None
            else {
                "model": prepared.semantic.model,
                "elapsed_ms": prepared.semantic.elapsed_ms,
                "context_messages": prepared.semantic.context_messages,
                "findings": [
                    {
                        "quote": p.quote,
                        "category": p.category,
                        "source": p.source,
                        "evidence_source": p.evidence_source,
                        "evidence_quote": p.evidence_quote,
                    }
                    for p in prepared.semantic.protections
                ],
            },
        }

    @app.post("/api/sessions/{session_id}/prepare")
    async def prepare(session_id: str, data: PrepareInput, request: Request) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        prepared = await service.prepare_message(
            session,
            data.text,
            tuple(data.terms),
            data.request_id,
            draft_identity=data.draft_identity,
            draft_facts=data.draft_facts,
        )
        return preview_view(prepared, session)

    @app.post("/api/sessions/{session_id}/prepared/{prepared_id}/decisions")
    async def review_preview(
        session_id: str, prepared_id: str, data: PreviewDecisionInput, request: Request
    ) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        receipt, prepared = service.review_preview(
            session,
            prepared_id,
            data.operation_id,
            data.expected_epoch,
            data.command,
        )
        return {
            "receipt": {
                "operation_id": data.operation_id,
                "old_prepared_id": receipt.old_prepared_id,
                "new_prepared_id": receipt.new_prepared_id,
                "body_sha256": receipt.body_sha256,
            },
            "preview_available": prepared is not None,
            "preview": None if prepared is None else preview_view(prepared, session),
        }

    @app.post("/api/sessions/{session_id}/cancel-prepare")
    async def cancel_prepare(
        session_id: str, data: SnapshotInput, request: Request
    ) -> dict[str, bool]:
        session = service.session(session_id, owner(request))
        service.cancel_prepare(session, data.prepared_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/send")
    async def send(session_id: str, data: SnapshotInput, request: Request) -> dict[str, str]:
        session = service.session(session_id, owner(request))
        result = await service.send(session, data.prepared_id)
        return {"content": result, "provider": session.provider.provider_id}

    @app.post("/api/sessions/{session_id}/send-stream")
    async def send_stream(
        session_id: str, data: SnapshotInput, request: Request
    ) -> StreamingResponse:
        session = service.session(session_id, owner(request))
        if not isinstance(session.provider, StreamingProvider):
            raise PrivacyError("STREAM_UNSUPPORTED")
        provider_id = session.provider.provider_id

        async def events() -> AsyncGenerator[bytes, None]:
            queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)

            async def observe(text: str) -> None:
                try:
                    await asyncio.wait_for(queue.put(text), timeout=5)
                except TimeoutError:
                    raise PrivacyError("STREAM_BACKPRESSURE") from None

            def record(kind: str, **fields: str) -> bytes:
                return (
                    json.dumps(
                        {"type": kind, "prepared_id": data.prepared_id, **fields},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")

            worker = asyncio.create_task(service.send(session, data.prepared_id, on_delta=observe))
            pending: asyncio.Task[str] | None = None
            try:
                while not worker.done() or not queue.empty():
                    pending = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        {worker, pending}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if pending in done:
                        yield record("delta", text=pending.result())
                    else:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    pending = None
                content = await worker
                yield record("complete", content=content, provider=provider_id)
            except PrivacyError as error:
                yield record("error", code=str(error))
            finally:
                with CancelScope(shield=True):
                    if pending is not None:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    if not worker.done():
                        service.cancel(session, data.prepared_id)
                        worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

        return OwnedStreamingResponse(events())

    @app.post("/api/sessions/{session_id}/discard")
    async def discard(session_id: str, data: SnapshotInput, request: Request) -> dict[str, bool]:
        service.discard(service.session(session_id, owner(request)), data.prepared_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str, data: SnapshotInput, request: Request) -> dict[str, bool]:
        service.cancel(service.session(session_id, owner(request)), data.prepared_id)
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def delete(session_id: str, request: Request) -> dict[str, bool]:
        service.delete(service.session(session_id, owner(request)))
        return {"ok": True}

    web = Path(__file__).resolve().parents[1] / "web"
    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/")
    async def index() -> Response:
        if not (web / "index.html").is_file():
            return JSONResponse(
                {"code": "UI_NOT_BUILT", "command": "npm --prefix frontend run build"},
                status_code=503,
            )
        return FileResponse(web / "index.html")

    return app
