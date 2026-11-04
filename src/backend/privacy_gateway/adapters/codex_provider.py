import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from privacy_gateway.domain.limits import MAX_HISTORY_MESSAGES, MAX_REQUEST_BYTES
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json

CLI_SHA256 = "f5521b058ca08e249afd3b25b4681e81100181d92e73aa6b94bb8e75c2aa487b"
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


@contextmanager
def request_directory() -> Iterator[str]:
    parent = Path(tempfile.gettempdir()).resolve()
    temporary = tempfile.TemporaryDirectory(prefix="censitive-codex-", dir=parent)
    owned = Path(temporary.name).resolve()
    if owned.parent != parent or not owned.name.startswith("censitive-codex-"):
        raise PrivacyError("CODEX_TEMP_INVALID")
    try:
        yield temporary.name
    finally:
        for attempt in range(40):
            if Path(temporary.name).resolve() != owned:
                raise PrivacyError("CODEX_TEMP_INVALID")
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 39:
                    raise PrivacyError("CODEX_CLEANUP_FAILED") from None
                time.sleep(0.05)


class CodexProvider:
    provider_id = "codex-chatgpt"
    model = "gpt-5.6-terra"
    serializer_version = "codex-stdin-v1"
    timeout = 120.0

    def __init__(
        self,
        executable: str | None = None,
        *,
        output_schema: Mapping[str, Any] | None = None,
    ) -> None:
        found = executable or shutil.which("codex")
        if not found:
            raise PrivacyError("CODEX_NOT_FOUND")
        self.executable = str(Path(found).resolve())
        self._output_schema_json: str | None = None
        self.output_schema_sha256: str | None = None
        if output_schema is not None:
            if not isinstance(output_schema, Mapping):
                raise PrivacyError("CODEX_OUTPUT_SCHEMA_INVALID")
            try:
                serialized = json.dumps(
                    dict(output_schema),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                parsed = json.loads(serialized)
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise PrivacyError("CODEX_OUTPUT_SCHEMA_INVALID") from None
            if type(parsed) is not dict:
                raise PrivacyError("CODEX_OUTPUT_SCHEMA_INVALID")
            self._output_schema_json = serialized
            self.output_schema_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            self.serializer_version = f"codex-stdin-v1-schema-{self.output_schema_sha256}"
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
        model["shell_type"] = "unified_exec"
        model.pop("web_search_tool_type", None)
        model["supports_search_tool"] = False
        model.pop("tool_mode", None)
        model.pop("multi_agent_version", None)
        model["include_skills_usage_instructions"] = False
        model["include_plugin_usage_instructions"] = False
        model["include_apps_usage_instructions"] = False
        model["base_instructions"] = ""
        model["apply_patch_tool_type"] = None
        model["experimental_supported_tools"] = []
        self.catalog = json.dumps({"models": [model]})

    @property
    def request_options(self) -> Mapping[str, object]:
        if self._output_schema_json is None or self.output_schema_sha256 is None:
            return {}
        return {
            "codex_output_schema": json.loads(self._output_schema_json),
            "codex_output_schema_sha256": self.output_schema_sha256,
        }

    @property
    def output_schema(self) -> Mapping[str, Any] | None:
        if self._output_schema_json is None:
            return None
        result: dict[str, Any] = json.loads(self._output_schema_json)
        return result

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
        ]
        if self._output_schema_json is not None:
            schema_path = directory / "output_schema.json"
            schema_path.write_text(self._output_schema_json + "\n", encoding="utf-8")
            command += ["--output-schema", str(schema_path)]
        command += [
            "--output-last-message",
            str(directory / "answer.txt"),
        ]
        for feature in DISABLED:
            command += ["--disable", feature]
        command += ["--enable", "skip_host_skill_discovery"]
        for key, value in config.items():
            command += ["-c", key + "=" + toml(value)]
        return command + ["-"]

    def _validate_body(self, body: bytes) -> None:
        if len(body) > MAX_REQUEST_BYTES:
            raise PrivacyError("INVALID_PAYLOAD")
        try:
            payload = strict_json(body)
        except PrivacyError:
            raise PrivacyError("INVALID_PAYLOAD") from None
        options = dict(self.request_options)
        if (
            type(payload) is not dict
            or set(payload) != {"model", "messages", *options}
            or payload.get("model") != self.model
            or not isinstance(payload.get("messages"), list)
            or not 1 <= len(payload["messages"]) <= MAX_HISTORY_MESSAGES
            or any(
                type(message) is not dict
                or set(message) != {"role", "content"}
                or not isinstance(message.get("role"), str)
                or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), str)
                for message in payload["messages"]
            )
            or any(payload[key] != value for key, value in options.items())
        ):
            raise PrivacyError("INVALID_PAYLOAD")

    async def complete(self, body: bytes) -> str:
        if self._busy:
            raise PrivacyError("PROVIDER_BUSY")
        self._busy = True
        try:
            self._validate_body(body)
            await asyncio.to_thread(self._verify_binary)
            await asyncio.to_thread(self.check_login)
            with request_directory() as folder:
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
