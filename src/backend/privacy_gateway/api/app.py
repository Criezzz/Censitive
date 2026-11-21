import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.application.gateway import Gateway, Prepared, Session
from privacy_gateway.domain.redaction import PrivacyError


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrepareInput(Input):
    text: str = Field(min_length=1)
    terms: list[str] = Field(default_factory=list)
    request_id: str | None = None


class SnapshotInput(Input):
    prepared_id: str


class AdjustInput(Input):
    released: list[str]
    masked: list[str]


class ProviderInput(Input):
    provider: str
    model: str


class CreateSessionInput(Input):
    provider: str | None = None
    model: str | None = None


class TitleInput(Input):
    title: str = Field(min_length=1)


def create_app(
    gateway: Gateway | None = None, connections: ProviderConnections | None = None
) -> FastAPI:
    service = gateway or Gateway()
    connections = connections or ProviderConnections(service.provider)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await connections.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.gateway = service
    clients: dict[str, str] = {}
    hosts = {"127.0.0.1:8765", "localhost:8765"}
    origins = {f"http://{host}" for host in hosts}

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.headers.get("host") not in hosts
            or (origin is not None and origin not in origins)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse({"code": "LOCAL_ACCESS_DENIED"}, status_code=403)
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            csrf = clients.get(request.cookies.get("censitive_owner", ""))
            if csrf is None or not secrets.compare_digest(
                csrf, request.headers.get("x-csrf-token", "")
            ):
                return JSONResponse({"code": "AUTH_REQUIRED"}, status_code=403)
            if request.method == "POST" and not request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return JSONResponse({"code": "TEXT_JSON_ONLY"}, status_code=415)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @app.exception_handler(PrivacyError)
    async def privacy_error(request: Request, error: PrivacyError):
        return JSONResponse({"code": str(error)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse({"code": "INVALID_INPUT"}, status_code=422)

    def owner(request: Request) -> str:
        value = request.cookies.get("censitive_owner", "")
        if value not in clients:
            raise PrivacyError("AUTH_REQUIRED")
        return value

    def identity(session: Session) -> dict[str, str]:
        return {
            "id": session.id,
            "provider": session.provider.provider_id,
            "model": session.provider.model,
        }

    def preview_view(prepared: Prepared) -> dict[str, object]:
        return {
            "id": prepared.id,
            "sanitized": prepared.method.sent,
            "body": prepared.body.decode("utf-8"),
            "method_changes": prepared.method.changes(prepared.original),
            "method_adjust": {
                "released": prepared.method.released,
                "masked": prepared.method.masked,
            },
        }

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request):
        value = request.cookies.get("censitive_owner", "")
        if value not in clients:
            value = secrets.token_hex(32)
            clients[value] = secrets.token_hex(32)
        response = JSONResponse(
            {
                "csrf": clients[value],
                "provider": service.provider.provider_id,
                "model": service.provider.model,
                "local_available": bool(service.method.key),
            }
        )
        response.set_cookie("censitive_owner", value, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/sessions")
    async def create_session(data: CreateSessionInput, request: Request):
        provider = service.provider
        if data.provider is not None or data.model is not None:
            provider = await connections.resolve(data.provider or "", data.model or "")
        return identity(service.create_session(owner(request), provider))

    @app.get("/api/sessions")
    async def list_sessions(request: Request):
        return {
            "sessions": [
                {
                    **identity(session),
                    "title": session.title,
                    "message_count": len(session.messages),
                    "provider_confirmed": True,
                }
                for session in service.list_sessions(owner(request))
            ]
        }

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        session = service.session(session_id, owner(request))
        return {
            **identity(session),
            "title": session.title,
            "messages": session.messages,
            "terms": session.terms,
            "provider_confirmed": True,
        }

    @app.post("/api/sessions/{session_id}/title")
    async def rename_session(session_id: str, data: TitleInput, request: Request):
        session = service.session(session_id, owner(request))
        service.rename_session(session, data.title)
        return {"id": session.id, "title": session.title}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request):
        service.delete(service.session(session_id, owner(request)))
        return {"ok": True}

    @app.get("/api/providers")
    async def providers(request: Request):
        owner(request)
        await connections.refresh_models()
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/check")
    async def check_connection(data: Input, request: Request):
        owner(request)
        await connections.check()
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/login")
    async def login(data: Input, request: Request):
        await connections.start_login(owner(request))
        return {"providers": connections.catalog()}

    @app.post("/api/providers/codex/login/cancel")
    async def cancel_login(data: Input, request: Request):
        await connections.cancel_login(owner(request))
        return {"providers": connections.catalog()}

    @app.post("/api/sessions/{session_id}/provider")
    async def select_provider(session_id: str, data: ProviderInput, request: Request):
        session = service.session(session_id, owner(request))
        service.discard(session)
        provider = await connections.resolve(data.provider, data.model)
        service.select_provider(session, provider)
        return identity(session)

    @app.post("/api/sessions/{session_id}/prepare")
    async def prepare(session_id: str, data: PrepareInput, request: Request):
        session = service.session(session_id, owner(request))
        prepared = await service.prepare_message(
            session, data.text, tuple(t.strip() for t in data.terms if t.strip()), data.request_id
        )
        return preview_view(prepared)

    @app.post("/api/sessions/{session_id}/prepared/{prepared_id}/adjust")
    async def adjust_method(session_id: str, prepared_id: str, data: AdjustInput, request: Request):
        session = service.session(session_id, owner(request))
        return preview_view(
            service.adjust_method(session, prepared_id, tuple(data.released), tuple(data.masked))
        )

    @app.post("/api/sessions/{session_id}/cancel-prepare")
    async def cancel_prepare(session_id: str, data: SnapshotInput, request: Request):
        service.cancel_prepare(service.session(session_id, owner(request)), data.prepared_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/send")
    async def send(session_id: str, data: SnapshotInput, request: Request):
        session = service.session(session_id, owner(request))
        return {
            "content": await service.send(session, data.prepared_id),
            "provider": session.provider.provider_id,
        }

    @app.post("/api/sessions/{session_id}/discard")
    async def discard(session_id: str, data: SnapshotInput, request: Request):
        service.discard(service.session(session_id, owner(request)), data.prepared_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str, data: SnapshotInput, request: Request):
        service.cancel(service.session(session_id, owner(request)), data.prepared_id)
        return {"ok": True}

    web = Path(__file__).resolve().parents[1] / "web"
    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/")
    async def index():
        if not (web / "index.html").is_file():
            return JSONResponse({"code": "UI_NOT_BUILT"}, status_code=503)
        return FileResponse(web / "index.html")

    return app
