import asyncio
import json
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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
from privacy_gateway.adapters.provider import StreamingProvider
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.models import SpanValidationError
from privacy_gateway.domain.redaction import PrivacyError
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


class SnapshotInput(StrictInput):
    prepared_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


class CorrectionInput(StrictInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    fact_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)
    expected_epoch: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=800)


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
            {"id": source.id, "revision": source.revision, "text": source.text}
            for source in state.sources
        ],
    }


class ProviderInput(StrictInput):
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)


class CreateSessionInput(StrictInput):
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    model: str | None = Field(default=None, min_length=1, max_length=100)


def create_app(
    gateway: Gateway | None = None,
    port: int = 8765,
    connections: ProviderConnections | None = None,
) -> FastAPI:
    service = gateway or Gateway()
    connections = connections or ProviderConnections(service.provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await connections.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.gateway = service
    clients: dict[str, str] = {}
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
            length = request.headers.get("content-length", "")
            if (
                not length.isascii()
                or not length.isdecimal()
                or len(length) > 6
                or int(length) > 100_000
            ):
                return JSONResponse({"code": "INPUT_TOO_LARGE"}, status_code=413)
            if request.method == "POST" and not request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return JSONResponse({"code": "TEXT_JSON_ONLY"}, status_code=415)
            received = bytearray()
            async for chunk in request.stream():
                received.extend(chunk)
                if len(received) > 100_000:
                    return JSONResponse({"code": "INPUT_TOO_LARGE"}, status_code=413)
            request._body = bytes(received)
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

    @app.exception_handler(SpanValidationError)
    async def span_error(_: Request, error: SpanValidationError) -> JSONResponse:
        return JSONResponse({"code": error.code.value}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
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
            if len(clients) >= 200:
                raise PrivacyError("CLIENT_LIMIT")
            value = secrets.token_hex(32)
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
            }
        )
        response.set_cookie("censitive_owner", value, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/sessions")
    async def create_session(data: CreateSessionInput, request: Request) -> dict[str, str]:
        client_owner = owner(request)
        selected = service.provider
        if data.provider is not None or data.model is not None:
            selected = await connections.resolve(data.provider or "", data.model or "")
        session = service.create_session(client_owner)
        session.provider = selected
        return {"id": session.id, "provider": selected.provider_id, "model": selected.model}

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

    @app.post("/api/sessions/{session_id}/prepare")
    async def prepare(session_id: str, data: PrepareInput, request: Request) -> dict[str, object]:
        session = service.session(session_id, owner(request))
        prepared = await service.prepare_message(
            session, data.text, tuple(data.terms), data.request_id
        )
        return {
            "id": prepared.id,
            "sanitized": prepared.sanitized,
            "body": prepared.body.decode("utf-8"),
            "expires_in": 300,
            "detections": [
                {
                    "start": d.start,
                    "end": d.end,
                    "category": d.category,
                    "value": data.text[d.start : d.end],
                    "restore_allowed": d.restore_allowed,
                }
                for d in prepared.detections
            ],
            "history_count": len(session.provider_history),
            "streaming": isinstance(session.provider, StreamingProvider),
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
