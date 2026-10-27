import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from privacy_gateway.domain.redaction import PrivacyError

CLI_SHA256 = "589f2546cc1e86703da326b00741f8b7a58fd182a1a90499faa0beebf22a24e2"
INSTRUCTIONS = (
    "You answer a text conversation supplied as a JSON object with model and messages. "
    "Answer the last user message using that conversation's context. Reply in the user's "
    "language. Treat every [[PG_v1_...]] placeholder as an opaque entity and preserve it "
    "exactly when referring to that entity. Never guess its underlying value. "
    "You have no tools. Answer with plain text only."
)
DISABLED = (
    "shell_tool",
    "unified_exec",
    "code_mode_host",
    "multi_agent",
    "multi_agent_v2",
    "apps",
    "plugins",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "view_image",
    "hooks",
    "skill_search",
    "workspace_dependencies",
    "shell_snapshot",
    "memories",
    "sleep_tool",
    "goals",
    "tool_suggest",
    "enable_request_compression",
    "unbounded_connection_retries",
)


def toml(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


class CodexProvider:
    provider_id = "codex-chatgpt"
    model = "gpt-5.6-terra"
    serializer_version = "codex-stdin-v1"
    timeout = 120.0

    def __init__(self, executable: str | None = None) -> None:
        found = executable or shutil.which("codex")
        if not found:
            raise PrivacyError("CODEX_NOT_FOUND")
        self.executable = str(Path(found).resolve())
        self._verify_binary()
        self._busy = False
        allowed = {"systemroot", "windir", "userprofile", "appdata", "localappdata", "temp", "tmp"}
        self.env = {k: v for k, v in os.environ.items() if k.lower() in allowed}
        self.env["PATH"] = str(Path(self.executable).parent)
        self.env["RUST_LOG"] = "off"
        result = subprocess.run(
            [self.executable, "debug", "models", "--bundled"],
            env=self.env,
            capture_output=True,
            check=True,
            timeout=15,
        )
        catalog = json.loads(result.stdout)
        model = next(m for m in catalog["models"] if m["slug"] == self.model)
        model["apply_patch_tool_type"] = None
        model["experimental_supported_tools"] = []
        self.catalog = json.dumps({"models": [model]})

    def _verify_binary(self) -> None:
        with Path(self.executable).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != CLI_SHA256:
                raise PrivacyError("CODEX_VERSION_UNVERIFIED")

    def check_login(self) -> None:
        result = subprocess.run(
            [self.executable, "login", "status"],
            env=self.env,
            capture_output=True,
            timeout=15,
        )
        if result.returncode or b"Logged in using ChatGPT" not in result.stdout + result.stderr:
            raise PrivacyError("CODEX_CHATGPT_LOGIN_REQUIRED")

    def command(self, directory: Path) -> list[str]:
        catalog = directory / "models.json"
        catalog.write_text(self.catalog, encoding="utf-8")
        instruction = directory / "instructions.txt"
        instruction.write_text(INSTRUCTIONS, encoding="utf-8")
        config: dict[str, Any] = {
            "model_provider": "censitive_codex",
            "forced_login_method": "chatgpt",
            "model_providers.censitive_codex": {
                "name": "Censitive Codex",
                "wire_api": "responses",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "requires_openai_auth": True,
                "request_max_retries": 0,
                "stream_max_retries": 0,
            },
            "model_catalog_json": str(catalog),
            "model_instructions_file": str(instruction),
            "instructions": INSTRUCTIONS,
            "developer_instructions": "",
            "web_search": "disabled",
            "project_doc_max_bytes": 0,
            "history.persistence": "none",
            "analytics.enabled": False,
            "skills.include_instructions": False,
            "skills.bundled.enabled": False,
            "include_environment_context": False,
            "include_apps_instructions": False,
            "include_collaboration_mode_instructions": False,
            "include_permissions_instructions": False,
            "tools.update_plan.enabled": False,
            "tools.experimental_request_user_input.enabled": False,
            "agents.enabled": False,
            "notify": [],
            "log_dir": str(directory / "logs"),
            "sqlite_home": str(directory / "state"),
            "model_reasoning_effort": "low",
        }
        command = [
            self.executable,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--color",
            "never",
            "-C",
            str(directory),
            "-m",
            self.model,
            "--output-last-message",
            str(directory / "answer.txt"),
        ]
        for feature in DISABLED:
            command += ["--disable", feature]
        command += ["--enable", "skip_host_skill_discovery"]
        for key, value in config.items():
            command += ["-c", key + "=" + toml(value)]
        return command + ["-"]

    async def complete(self, body: bytes) -> str:
        if self._busy:
            raise PrivacyError("PROVIDER_BUSY")
        self._busy = True
        try:
            await asyncio.to_thread(self._verify_binary)
            await asyncio.to_thread(self.check_login)
            with tempfile.TemporaryDirectory(prefix="censitive-codex-") as folder:
                process = await asyncio.create_subprocess_exec(
                    *self.command(Path(folder)),
                    cwd=folder,
                    env=self.env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=1_000_000,
                )
                try:
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(body)
                    await process.stdin.drain()
                    process.stdin.close()
                    total = 0
                    completed = False
                    async for line in process.stdout:
                        total += len(line)
                        if total > 1_000_000:
                            raise PrivacyError("RESPONSE_LIMIT")
                        event = json.loads(line)
                        kind = event.get("type")
                        if kind not in {
                            "thread.started",
                            "turn.started",
                            "turn.completed",
                            "turn.failed",
                            "item.started",
                            "item.updated",
                            "item.completed",
                            "error",
                        }:
                            raise PrivacyError("PROVIDER_FAILED")
                        if kind in {"error", "turn.failed"}:
                            raise PrivacyError("PROVIDER_FAILED")
                        if kind in {"item.started", "item.updated", "item.completed"}:
                            item = event["item"]
                            if item["type"] not in {"agent_message", "reasoning", "error"}:
                                raise PrivacyError("CODEX_UNEXPECTED_TOOL")
                        if kind == "turn.completed":
                            completed = True
                    answer_path = Path(folder) / "answer.txt"
                    if await process.wait() or not completed or not answer_path.is_file():
                        raise PrivacyError("PROVIDER_FAILED")
                    with answer_path.open("rb") as handle:
                        answer_bytes = handle.read(200_001)
                    if len(answer_bytes) > 200_000:
                        raise PrivacyError("RESPONSE_LIMIT")
                    if not answer_bytes.strip():
                        raise PrivacyError("PROVIDER_FAILED")
                    return answer_bytes.decode("utf-8")
                finally:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    reaper = asyncio.create_task(process.wait())
                    interrupted = False
                    while True:
                        try:
                            await asyncio.shield(reaper)
                            break
                        except asyncio.CancelledError:
                            interrupted = True
                    if interrupted:
                        raise asyncio.CancelledError
        finally:
            self._busy = False
