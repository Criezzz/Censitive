"""Loopback-only API with strict DTOs, owner cookies and CSRF checks."""

import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.models import SpanValidationError
from privacy_gateway.domain.redaction import PrivacyError


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrepareInput(StrictInput):
    text: str = Field(min_length=1, max_length=20_000)
    terms: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=30
    )
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class SnapshotInput(StrictInput):
    prepared_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


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
                "mode": "rules-manual-semantic" if service.detector else "rules-and-manual",
                "detector": service.detector.model if service.detector else "rules",
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
        }

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
