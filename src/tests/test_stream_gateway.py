import asyncio
import json
from collections.abc import AsyncGenerator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message


class StreamProvider(FakeProvider):
    request_options = {"stream": True}

    def __init__(self, *, bad: bool = False) -> None:
        self.bad = bad
        self.closed = False
        self.calls: list[bytes] = []

    async def stream(self, body: bytes) -> AsyncGenerator[str, None]:
        self.calls.append(body)
        try:
            text = json.loads(body)["messages"][-1]["content"]
            for letter in text:
                yield letter
            if self.bad:
                yield "[[PG_v1_"
        finally:
            self.closed = True


@pytest.mark.parametrize("observe", [False, True])
def test_same_transaction_full_and_stream_preserves_exact_approval_restore_commit(
    observe: bool,
) -> None:
    async def run() -> None:
        provider = StreamProvider()
        gateway = Gateway(provider)
        session = gateway.create_session("owner")
        prepared = gateway.prepare(session, "Hello Mai 🙂", ("Mai",))
        seen = []

        async def receive(text: str) -> None:
            assert "[[PG_" not in text
            seen.append(text)
            assert session.private_history == []

        result = await gateway.send(session, prepared.id, on_delta=receive if observe else None)
        assert result == "Hello Mai 🙂"
        assert "".join(seen) == result if observe else seen == []
        assert provider.calls == [prepared.body] and provider.closed
        assert len(session.private_history) == 2 and not session.busy
        with pytest.raises(PrivacyError, match="STALE_PREVIEW"):
            await gateway.send(session, prepared.id)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["token", "observer", "cancel"])
def test_partial_failure_rolls_back_and_closes_iterator(failure: str) -> None:
    async def run() -> None:
        provider = StreamProvider(bad=failure == "token")
        gateway = Gateway(provider)
        session = gateway.create_session("owner")
        prepared = gateway.prepare(session, "public Mai", ("Mai",))
        seen = []

        async def receive(text: str) -> None:
            seen.append(text)
            if failure == "observer":
                raise PrivacyError("STREAM_BACKPRESSURE")
            if failure == "cancel":
                gateway.cancel(session, prepared.id)
                await asyncio.sleep(0)

        with pytest.raises(PrivacyError):
            await gateway.send(session, prepared.id, on_delta=receive)
        assert seen and provider.closed and not session.busy
        assert not session.private_history and not session.provider_history and not session.vault
        assert session.prepared is None

    asyncio.run(run())


@pytest.mark.parametrize("bad", [False, True])
def test_stream_api_typed_terminal_and_committed_history(bad: bool) -> None:
    provider = StreamProvider(bad=bad)
    gateway = Gateway(provider)
    with TestClient(create_app(gateway), base_url="http://localhost:8765") as api:
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        sid = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        path = f"/api/sessions/{sid}"
        prepared = api.post(
            path + "/prepare", json={"text": "Hello Mai", "terms": ["Mai"]}, headers=headers
        ).json()
        assert prepared["streaming"] is True
        denied = api.post(path + "/send-stream", json={"prepared_id": prepared["id"]})
        assert denied.status_code == 403 and not provider.calls
        response = api.post(
            path + "/send-stream", json={"prepared_id": prepared["id"]}, headers=headers
        )
        records = [json.loads(line) for line in response.text.splitlines()]
        assert all(item["prepared_id"] == prepared["id"] for item in records)
        assert records[-1]["type"] == ("error" if bad else "complete")
        assert all(item["type"] == "delta" for item in records[:-1])
        assert "".join(item["text"] for item in records[:-1]) == "Hello Mai"
        assert len(api.get(path).json()["messages"]) == (0 if bad else 2)
        assert provider.closed


def test_stream_observer_disconnect_cancels_worker_and_rolls_back() -> None:
    from privacy_gateway.api.app import SnapshotInput

    provider = StreamProvider()
    gateway = Gateway(provider)
    app = create_app(gateway)
    with TestClient(app, base_url="http://localhost:8765") as api:
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        sid = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        prepared = api.post(
            f"/api/sessions/{sid}/prepare", json={"text": "public " * 100}, headers=headers
        ).json()
        cookie = api.cookies.get("censitive_owner")
        endpoint = next(
            route.endpoint
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.endswith("/send-stream")
        )

        async def disconnect() -> None:
            response = await endpoint(
                sid,
                SnapshotInput(prepared_id=prepared["id"]),
                Request(
                    {"type": "http", "headers": [(b"cookie", f"censitive_owner={cookie}".encode())]}
                ),
            )
            first = await anext(response.body_iterator)
            assert json.loads(first)["type"] == "delta"
            await response.body_iterator.aclose()
            session = gateway.sessions[sid]
            assert provider.closed and not session.busy
            assert not session.private_history and not session.provider_history
            assert session.prepared is None

        asyncio.run(disconnect())


@pytest.mark.parametrize("version,blocked_send", [("2.3", False), ("2.3", True), ("2.4", False)])
def test_actual_asgi_disconnect_reaps_stream_worker(version: str, blocked_send: bool) -> None:
    from privacy_gateway.api.app import SnapshotInput

    class PausedProvider(StreamProvider):
        async def stream(self, body: bytes) -> AsyncGenerator[str, None]:
            try:
                yield "partial"
                await asyncio.Event().wait()
            finally:
                self.closed = True

    provider = PausedProvider()
    gateway = Gateway(provider)
    app = create_app(gateway)
    with TestClient(app, base_url="http://localhost:8765") as api:
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        sid = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        owner = api.cookies.get("censitive_owner")
    session = gateway.sessions[sid]
    prepared = gateway.prepare(session, "public", ())
    endpoint = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.endswith("/send-stream")
    )

    async def run() -> None:
        request = Request(
            {"type": "http", "headers": [(b"cookie", f"censitive_owner={owner}".encode())]}
        )
        response = await endpoint(sid, SnapshotInput(prepared_id=prepared.id), request)
        first = asyncio.Event()

        async def receive() -> Message:
            await first.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                first.set()
                if version == "2.4":
                    raise OSError("synthetic disconnect")
                if blocked_send:
                    await asyncio.Event().wait()

        try:
            await asyncio.wait_for(
                response({"type": "http", "asgi": {"spec_version": version}}, receive, send),
                timeout=2,
            )
        except ClientDisconnect:
            pass
        assert provider.closed and not session.busy
        assert not session.private_history and not session.vault and session.prepared is None

    asyncio.run(run())
