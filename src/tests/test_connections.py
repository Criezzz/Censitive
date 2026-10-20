import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from privacy_gateway.adapters.codex_provider import CodexProvider
from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError


class SyntheticCodex(CodexProvider):
    def __init__(self) -> None:
        self.logged_in = True
        self.calls: list[bytes] = []
        self.executable = "synthetic-codex.exe"
        self.env = {"SYNTHETIC": "true"}

    def _verify_binary(self) -> None:
        pass

    def check_login(self) -> None:
        if not self.logged_in:
            raise PrivacyError("CODEX_CHATGPT_LOGIN_REQUIRED")

    async def complete(self, body: bytes) -> str:
        self.calls.append(body)
        return str(json.loads(body)["messages"][-1]["content"])


def test_select_provider_is_session_scoped_consumes_no_model_call_and_invalidates_preview() -> None:
    codex = SyntheticCodex()
    gateway = Gateway()
    connections = ProviderConnections(gateway.provider, lambda: codex)
    with TestClient(
        create_app(gateway, connections=connections), base_url="http://localhost:8765"
    ) as api:
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        first = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        second = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        path = f"/api/sessions/{first}"
        old = api.post(
            path + "/prepare", json={"text": "Lan", "terms": ["Lan"]}, headers=headers
        ).json()
        check = api.post("/api/providers/codex/check", json={}, headers=headers)
        assert check.json()["providers"][1]["status"] == "ready" and codex.calls == []
        result = api.post(
            path + "/provider",
            json={"provider": "codex-chatgpt", "model": codex.model},
            headers=headers,
        )
        assert result.status_code == 200 and result.json()["provider"] == "codex-chatgpt"
        assert codex.calls == []
        assert api.get(f"/api/sessions/{second}").json()["provider"] == "local-demo"
        assert (
            api.post(path + "/send", json={"prepared_id": old["id"]}, headers=headers).json()[
                "code"
            ]
            == "STALE_PREVIEW"
        )
        new = api.post(
            path + "/prepare", json={"text": "Lan", "terms": ["Lan"]}, headers=headers
        ).json()
        assert json.loads(new["body"])["model"] == codex.model
        assert (
            api.post(path + "/send", json={"prepared_id": new["id"]}, headers=headers).json()[
                "content"
            ]
            == "Lan"
        )
        assert len(codex.calls) == 1 and b"Lan" not in codex.calls[0]
        api.post(
            path + "/provider",
            json={"provider": "local-demo", "model": "template-v1"},
            headers=headers,
        )
        view = api.get(path).json()
        assert view["provider"] == "local-demo"
        assert view["messages"][-1]["provider"] == "codex-chatgpt"


@pytest.mark.parametrize(
    "endpoint",
    ["/providers/codex/check", "/providers/codex/login", "/providers/codex/login/cancel"],
)
def test_connection_commands_require_csrf_and_reject_extra_fields(endpoint: str) -> None:
    with TestClient(create_app(), base_url="http://localhost:8765") as api:
        assert api.get("/api/providers").status_code == 409
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        assert api.post("/api" + endpoint, json={}).status_code == 403
        response = api.post(
            "/api" + endpoint, json={"transcript": "SYNTHETIC_PRIVATE"}, headers=headers
        )
        assert response.status_code == 422 and "SYNTHETIC_PRIVATE" not in response.text


def test_switch_checks_owner_and_busy_before_connection_work() -> None:
    gateway = Gateway()

    def forbidden_factory() -> CodexProvider:
        pytest.fail("Must not inspect Codex before ownership/busy validation")

    app = create_app(gateway, connections=ProviderConnections(gateway.provider, forbidden_factory))
    with (
        TestClient(app, base_url="http://localhost:8765") as first,
        TestClient(app, base_url="http://localhost:8765") as other,
    ):
        headers = {"X-CSRF-Token": first.get("/api/bootstrap").json()["csrf"]}
        alien = {"X-CSRF-Token": other.get("/api/bootstrap").json()["csrf"]}
        sid = first.post("/api/sessions", json={}, headers=headers).json()["id"]
        body = {"provider": "codex-chatgpt", "model": CodexProvider.model}
        assert (
            other.post(f"/api/sessions/{sid}/provider", json=body, headers=alien).json()["code"]
            == "SESSION_NOT_FOUND"
        )
        gateway.sessions[sid].active_id = "synthetic_active"
        assert (
            first.post(f"/api/sessions/{sid}/provider", json=body, headers=headers).json()["code"]
            == "SESSION_BUSY"
        )


def test_failed_connection_or_unknown_model_does_not_mutate_session() -> None:
    codex = SyntheticCodex()
    codex.logged_in = False
    gateway = Gateway()
    connections = ProviderConnections(gateway.provider, lambda: codex)
    with TestClient(
        create_app(gateway, connections=connections), base_url="http://localhost:8765"
    ) as api:
        headers = {"X-CSRF-Token": api.get("/api/bootstrap").json()["csrf"]}
        sid = api.post("/api/sessions", json={}, headers=headers).json()["id"]
        for model, code in [
            ("unapproved-model", "UNKNOWN_PROVIDER_MODEL"),
            (codex.model, "CODEX_CHATGPT_LOGIN_REQUIRED"),
        ]:
            result = api.post(
                f"/api/sessions/{sid}/provider",
                json={"provider": "codex-chatgpt", "model": model},
                headers=headers,
            )
            assert result.json()["code"] == code
            assert gateway.sessions[sid].provider.provider_id == "local-demo"
        assert not codex.calls


def test_deleted_session_cannot_be_reactivated_after_async_connection_check() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        codex = SyntheticCodex()

        class DelayedConnections(ProviderConnections):
            async def resolve(self, provider_id: str, model: str) -> CodexProvider:
                entered.set()
                await release.wait()
                return codex

        gateway = Gateway()
        app = create_app(gateway, connections=DelayedConnections(gateway.provider))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8765"
        ) as api:
            headers = {"X-CSRF-Token": (await api.get("/api/bootstrap")).json()["csrf"]}
            sid = (await api.post("/api/sessions", json={}, headers=headers)).json()["id"]
            pending = asyncio.create_task(
                api.post(
                    f"/api/sessions/{sid}/provider",
                    json={"provider": "codex-chatgpt", "model": codex.model},
                    headers=headers,
                )
            )
            await entered.wait()
            await api.request("DELETE", f"/api/sessions/{sid}", json={}, headers=headers)
            release.set()
            assert (await pending).json()["code"] == "SESSION_NOT_FOUND"
            assert sid not in gateway.sessions

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "timeout"])
def test_login_lifecycle_owned_official_process_no_chat_or_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    async def scenario() -> None:
        codex = SyntheticCodex()
        codex.logged_in = False
        connections = ProviderConnections(FakeProvider(), lambda: codex)
        entered, release = asyncio.Event(), asyncio.Event()

        class Process:
            returncode: int | None = None
            killed = False

            async def wait(self) -> int:
                await release.wait()
                if self.returncode is None:
                    self.returncode = 1 if outcome == "failure" else 0
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.returncode = -1
                release.set()

        process = Process()

        async def spawn(*args: str, **kwargs: Any) -> Process:
            assert args == (codex.executable, "login", "-c", 'forced_login_method="chatgpt"')
            assert (
                kwargs["stdin"]
                == kwargs["stdout"]
                == kwargs["stderr"]
                == asyncio.subprocess.DEVNULL
            )
            entered.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        if outcome == "timeout":
            original_wait = asyncio.wait_for

            async def short_wait(awaitable: Any, timeout: float) -> Any:
                assert timeout == 300
                return await original_wait(awaitable, timeout=0.2)

            monkeypatch.setattr(asyncio, "wait_for", short_wait)
        await connections.start_login("owner")
        await entered.wait()
        assert connections.status == "signing_in"
        with pytest.raises(PrivacyError, match="LOGIN_BUSY"):
            await connections.cancel_login("alien")
        with pytest.raises(PrivacyError, match="LOGIN_BUSY"):
            await connections.start_login("alien")
        if outcome == "cancel":
            await connections.cancel_login("owner")
            await connections.cancel_login("owner")
            assert process.killed and connections.code == "CODEX_LOGIN_CANCELLED"
        elif outcome == "timeout":
            assert connections.login_task is not None
            await connections.login_task
            assert process.killed and connections.code == "CODEX_LOGIN_TIMEOUT"
            assert connections.status == "needs_login"
        else:
            codex.logged_in = outcome == "success"
            release.set()
            assert connections.login_task is not None
            await connections.login_task
            assert connections.status == ("ready" if outcome == "success" else "needs_login")
        assert not codex.calls
        await connections.close()

    asyncio.run(scenario())


def test_connection_error_is_fixed_code() -> None:
    def fail() -> CodexProvider:
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    connection = ProviderConnections(FakeProvider(), fail)
    asyncio.run(connection.check())
    assert connection.status == "unavailable" and connection.code == "CONNECTION_FAILED"
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(connection.catalog())


@pytest.mark.parametrize("shutdown", [False, True])
def test_immediate_login_cancel_before_coroutine_starts(shutdown: bool) -> None:
    async def scenario() -> None:
        codex = SyntheticCodex()
        codex.logged_in = False
        connection = ProviderConnections(FakeProvider(), lambda: codex)
        await connection.start_login("owner")
        if shutdown:
            await connection.close()
        else:
            await connection.cancel_login("owner")
        assert connection.status == "needs_login"
        assert connection.code == "CODEX_LOGIN_CANCELLED"
        assert connection.login_task is not None and connection.login_task.done()
        await connection.cancel_login("owner")
        assert not codex.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("intervening", ["switch", "prepare"])
def test_delayed_switch_cannot_overwrite_new_session_state(intervening: str) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        codex = SyntheticCodex()

        class DelayedConnections(ProviderConnections):
            async def resolve(self, provider_id: str, model: str) -> Any:
                if provider_id == "codex-chatgpt":
                    entered.set()
                    await release.wait()
                    return codex
                return self.demo

        gateway = Gateway()
        app = create_app(gateway, connections=DelayedConnections(gateway.provider))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8765"
        ) as api:
            headers = {"X-CSRF-Token": (await api.get("/api/bootstrap")).json()["csrf"]}
            sid = (await api.post("/api/sessions", json={}, headers=headers)).json()["id"]
            path = f"/api/sessions/{sid}"
            pending = asyncio.create_task(
                api.post(
                    path + "/provider",
                    json={"provider": "codex-chatgpt", "model": codex.model},
                    headers=headers,
                )
            )
            await entered.wait()
            if intervening == "switch":
                assert (
                    await api.post(
                        path + "/provider",
                        json={
                            "provider": "local-demo",
                            "model": "template-v1",
                        },
                        headers=headers,
                    )
                ).status_code == 200
            preview = (
                await api.post(
                    path + "/prepare",
                    json={
                        "text": "Lan",
                        "terms": ["Lan"],
                    },
                    headers=headers,
                )
            ).json()
            release.set()
            assert (await pending).json()["code"] == "SESSION_CHANGED"
            assert gateway.sessions[sid].provider.provider_id == "local-demo"
            current = gateway.sessions[sid].prepared
            assert current is not None and current.id == preview["id"]
            assert not codex.calls

    asyncio.run(scenario())
