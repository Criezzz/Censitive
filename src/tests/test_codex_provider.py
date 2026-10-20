import asyncio
import hashlib
import json
import shutil
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from privacy_gateway.adapters.codex_provider import CLI_SHA256, CodexProvider, toml
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError


@pytest.fixture(scope="module")
def codex() -> CodexProvider:
    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("Optional Codex CLI is not installed")
    with Path(executable).open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != CLI_SHA256:
            pytest.skip("Installed Codex build is not the verified build")
    return CodexProvider(executable)


@pytest.fixture
def loopback(codex: CodexProvider, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"requests": [], "mode": "echo", "entered": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_POST(self) -> None:
            assert "Authorization" not in self.headers
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(payload)
            state["entered"].set()
            mode = state["mode"]
            if mode in {"429", "disconnect"}:
                if mode == "429":
                    self.send_response(429)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.connection.close()
                    self.close_connection = True
                return
            if mode == "wait":
                state["release"].wait(timeout=10)
                return
            user = next(x for x in payload["input"] if x.get("role") == "user")
            body = json.loads(user["content"][0]["text"])
            text = body["messages"][-1]["content"]
            if mode == "oversize":
                text = "x" * 210_000
            item: dict[str, Any] = {
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
            if mode in {"patch", "exec"} and len(state["requests"]) == 1:
                item = {
                    "type": "custom_tool_call",
                    "id": "ctc_test",
                    "call_id": "call_test",
                    "name": "apply_patch",
                    "input": state.get("patch", "*** Begin Patch\n*** End Patch"),
                }
                if mode == "exec":
                    item.update(name="exec", namespace="functions", input="text(ALL_TOOLS)")
            events: list[dict[str, Any]] = [
                {
                    "type": "response.created",
                    "response": {"id": "resp_test", "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.added", "output_index": 0, "item": item},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_test",
                        "status": "completed",
                        "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
            ]
            if mode == "commentary":
                commentary = {
                    "type": "message",
                    "id": "msg_commentary",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "DO_NOT_DISPLAY_COMMENTARY"}],
                }
                item["phase"] = "final_answer"
                events[1:1] = [
                    {"type": "response.output_item.added", "output_index": 0, "item": commentary},
                    {"type": "response.output_item.done", "output_index": 0, "item": commentary},
                ]
            data = "".join(
                f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = codex.command

    def offline_command(directory: Path) -> list[str]:
        state["directory"] = directory
        (directory / "AGENTS.md").write_text("SYNTHETIC_UNAPPROVED_INSTRUCTIONS", encoding="utf-8")
        (directory / ".codex").mkdir()
        (directory / ".codex" / "config.toml").write_text(
            'developer_instructions = "SYNTHETIC_UNAPPROVED_CONFIG"', encoding="utf-8"
        )
        config = {
            "model_provider": "offline_test",
            "model_providers.offline_test": {
                "name": "offline_test",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "wire_api": "responses",
                "requires_openai_auth": False,
                "request_max_retries": 0,
                "stream_max_retries": 0,
            },
        }
        command = original(directory)[:-1]
        for key, value in config.items():
            command += ["-c", key + "=" + toml(value)]
        return command + ["-"]

    monkeypatch.setattr(codex, "command", offline_command)
    monkeypatch.setattr(codex, "check_login", lambda: None)
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_cli_only_receives_approved_body_and_fixed_context(
    codex: CodexProvider,
    loopback: dict[str, Any],
) -> None:
    async def scenario() -> None:
        gateway = Gateway(codex)
        session = gateway.create_session("owner")
        prepared = gateway.prepare(session, "Email Lan: lan@example.com", ("Lan",))
        assert await gateway.send(session, prepared.id) == "Email Lan: lan@example.com"
        assert len(loopback["requests"]) == 1
        payload = loopback["requests"][0]
        user = next(x for x in payload["input"] if x.get("role") == "user")
        assert user["content"][0]["text"].encode() == prepared.body
        whole = json.dumps(payload)
        assert "lan@example.com" not in whole and "Lan" not in whole
        assert "apply_patch" not in whole
        assert "declare const tools" not in whole
        assert "SYNTHETIC_UNAPPROVED" not in whole
        assert str(Path.cwd()) not in whole.replace("\\\\", "\\")
        assert not loopback["directory"].exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["429", "disconnect", "oversize"])
def test_cli_failure_does_not_retry_or_commit(
    codex: CodexProvider,
    loopback: dict[str, Any],
    mode: str,
) -> None:
    loopback["mode"] = mode
    gateway = Gateway(codex)
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "public", ())
    with pytest.raises(PrivacyError):
        asyncio.run(gateway.send(session, prepared.id))
    assert len(loopback["requests"]) == 1
    assert not session.private_history and not session.vault
    assert not loopback["directory"].exists()


@pytest.mark.parametrize("mode", ["patch", "exec"])
def test_cli_tools_cannot_execute(
    codex: CodexProvider,
    loopback: dict[str, Any],
    tmp_path: Path,
    mode: str,
) -> None:
    loopback["mode"] = mode
    canary = tmp_path / "synthetic-canary.txt"
    canary.write_text("SYNTHETIC_CANARY", encoding="utf-8")
    loopback["patch"] = (
        f"*** Begin Patch\n*** Update File: {canary.as_posix()}\n"
        "@@\n-SYNTHETIC_CANARY\n+CHANGED\n*** End Patch"
    )
    result = asyncio.run(
        codex.complete(b'{"model":"gpt-5.6-terra","messages":[{"role":"user","content":"public"}]}')
    )
    assert result == "public"
    assert len(loopback["requests"]) == 2
    followup = json.dumps(loopback["requests"][1])
    expected = (
        "unsupported custom tool call: apply_patch"
        if mode == "patch"
        else "code-mode host is disabled"
    )
    assert expected in followup
    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_CANARY"


def test_cli_cancel_reaps_process_and_removes_temporary_directory(
    codex: CodexProvider,
    loopback: dict[str, Any],
) -> None:
    loopback["mode"] = "wait"
    loopback["release"] = threading.Event()

    async def scenario() -> None:
        gateway = Gateway(codex)
        session = gateway.create_session("owner")
        request = gateway.prepare(session, "public", ())
        pending = asyncio.create_task(gateway.send(session, request.id))
        assert await asyncio.to_thread(loopback["entered"].wait, 15)
        gateway.cancel(session, request.id)
        gateway.cancel(session, request.id)
        with pytest.raises(PrivacyError, match="CANCELLED"):
            await asyncio.wait_for(pending, timeout=5)
        loopback["release"].set()
        assert not loopback["directory"].exists()
        assert not session.private_history and not session.busy

    asyncio.run(scenario())


def test_unverified_executable_is_rejected_before_launch(tmp_path: Path) -> None:
    executable = tmp_path / "unverified.exe"
    executable.write_bytes(b"not a trusted executable")
    with pytest.raises(PrivacyError, match="CODEX_VERSION_UNVERIFIED"):
        CodexProvider(str(executable))


def test_cli_displays_final_answer_only(codex: CodexProvider, loopback: dict[str, Any]) -> None:
    loopback["mode"] = "commentary"
    result = asyncio.run(
        codex.complete(
            b'{"model":"gpt-5.6-terra","messages":[{"role":"user","content":"final text"}]}'
        )
    )
    assert result == "final text"
