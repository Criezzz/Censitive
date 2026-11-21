import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from privacy_gateway.domain.redaction import PrivacyError

INSTRUCTIONS = (
    "You answer a text conversation supplied as a JSON object with model and messages. "
    "Answer the last user message using that conversation's context. Reply in the user's "
    "language. Treat every bracketed placeholder as an opaque entity and preserve it "
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
    parent = Path(__file__).resolve().parents[3] / ".local/runtime"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="censitive-codex-", dir=parent) as directory:
        yield directory


def native_codex(found: Path) -> str:
    if found.suffix.lower() == ".exe":
        return str(found)
    vendored = sorted(found.parent.glob("node_modules/@openai/**/vendor/*/bin/codex.exe"))
    if not vendored:
        raise PrivacyError("CODEX_NOT_FOUND")
    return str(vendored[0])


class CodexProvider:
    provider_id = "codex-chatgpt"
    model = "gpt-5.6-terra"
    timeout = 120.0

    def __init__(
        self,
        executable: str | None = None,
        *,
        model: str | None = None,
    ) -> None:
        if model:
            self.model = model
        found = executable or shutil.which("codex")
        if not found:
            raise PrivacyError("CODEX_NOT_FOUND")
        self.executable = native_codex(Path(found).resolve())
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
        self.models = [m["slug"] for m in catalog["models"] if m["slug"] != "codex-auto-review"]
        model = next((m for m in catalog["models"] if m["slug"] == self.model), None)
        if model is None:
            raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
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

    def body(self, text: str) -> bytes:
        return json.dumps(
            {"model": self.model, "messages": [{"role": "user", "content": text}]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

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

    async def complete(self, body: bytes) -> str:
        await asyncio.to_thread(self.check_login)
        with request_directory() as folder:
            process = await asyncio.create_subprocess_exec(
                *self.command(Path(folder)),
                cwd=folder,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(body), timeout=self.timeout)
                completed = False
                for line in output.splitlines():
                    event = json.loads(line)
                    if event.get("type") in {"error", "turn.failed"}:
                        raise PrivacyError("PROVIDER_FAILED")
                    if event.get("type") in {
                        "item.started",
                        "item.updated",
                        "item.completed",
                    } and event["item"]["type"] not in {"agent_message", "reasoning", "error"}:
                        raise PrivacyError("CODEX_UNEXPECTED_TOOL")
                    completed |= event.get("type") == "turn.completed"
                answer_path = Path(folder) / "answer.txt"
                if process.returncode or not completed or not answer_path.is_file():
                    raise PrivacyError("PROVIDER_FAILED")
                answer = answer_path.read_text(encoding="utf-8")
                if not answer.strip():
                    raise PrivacyError("PROVIDER_FAILED")
                return answer
            except TimeoutError:
                raise PrivacyError("PROVIDER_TIMEOUT") from None
            except (ValueError, KeyError, OSError) as error:
                if isinstance(error, PrivacyError):
                    raise
                raise PrivacyError("PROVIDER_FAILED") from None
            finally:
                if process.returncode is None:
                    process.kill()
                await process.wait()
