import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json

EXECUTABLE_ENV = "CENSITIVE_ANTIGRAVITY_EXECUTABLE"
CLI_SHA256 = "fbbfb3f15c7628252c5e12765683c688ba05ee122b853cadeb0bbfd4e13ca25b"
MODEL_ID = "gemini-3.8-flash-medium"
MAX_OUTPUT_BYTES = 256_000
PROCESS_TIMEOUT = 15.0
MAX_GROUPS = 16
MAX_BUCKETS = 8
MAX_QUOTA_BUCKETS = MAX_GROUPS * MAX_BUCKETS
_KNOWN_GEMINI_BUCKETS = {"gemini-weekly": "weekly", "gemini-5h": "5h"}
_MODEL_LINE = re.compile(r"(?m)^gemini-3\.8-flash-medium(?:\s|$)")
_RESET_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")


class AntigravityConnection:
    provider_id = "antigravity-google"
    name = "Antigravity Google account"
    auth = "google-cli"

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = Path(executable) if executable else self._discover_executable()
        self.status = "unchecked" if self.executable is not None else "unavailable"
        self.code = "" if self.executable is not None else "ANTIGRAVITY_NOT_FOUND"
        self.authenticated = False
        self.model = ""
        self.quota: list[dict[str, object]] = []
        self.lock = asyncio.Lock()

    @staticmethod
    def _discover_executable() -> Path | None:
        configured = os.environ.get(EXECUTABLE_ENV, "").strip()
        if configured:
            return Path(configured)
        discovered = shutil.which("agy")
        return Path(discovered) if discovered else None

    def catalog(self) -> dict[str, object]:
        return {
            "id": self.provider_id,
            "name": self.name,
            "model": self.model,
            "auth": self.auth,
            "status": self.status,
            "code": self.code,
            "authenticated": self.authenticated,
            "quota": [dict(bucket) for bucket in self.quota],
            "streaming": False,
        }

    def _environment(self) -> dict[str, str]:
        allowed = {
            "SYSTEMROOT",
            "WINDIR",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
        }
        environment = {
            key: value for key, value in os.environ.items() if key.upper() in allowed and value
        }
        if self.executable is not None:
            environment["PATH"] = str(self.executable.parent)
        environment["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
        return environment

    def _verify_binary(self) -> None:
        if self.executable is None:
            raise PrivacyError("ANTIGRAVITY_NOT_FOUND")
        try:
            with self.executable.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError:
            raise PrivacyError("ANTIGRAVITY_NOT_FOUND") from None
        if digest != CLI_SHA256:
            raise PrivacyError("ANTIGRAVITY_VERSION_UNVERIFIED")

    async def _run(
        self,
        args: list[str],
        workspace: Path,
    ) -> bytes:
        assert self.executable is not None
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.executable),
                *args,
                cwd=workspace,
                env=self._environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_OUTPUT_BYTES + 1,
            )
        except (OSError, subprocess.SubprocessError):
            raise PrivacyError("ANTIGRAVITY_METADATA_FAILED") from None
        tasks: list[asyncio.Task[Any]] = []
        wait_task: asyncio.Task[Any] | None = None
        try:
            if process.stdout is None or process.stderr is None:
                raise PrivacyError("ANTIGRAVITY_METADATA_FAILED")
            wait_task = asyncio.create_task(process.wait())
            stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
            stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
            tasks = [stdout_task, stderr_task, wait_task]
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, wait_task), timeout=PROCESS_TIMEOUT
            )
            if process.returncode != 0:
                raise PrivacyError("ANTIGRAVITY_METADATA_FAILED")
            return stdout
        except TimeoutError:
            raise PrivacyError("ANTIGRAVITY_METADATA_TIMEOUT") from None
        except asyncio.CancelledError:
            raise
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass
            interrupted = False
            reaper = wait_task
            if reaper is None or reaper.cancelled():
                reaper = asyncio.create_task(process.wait())
            while True:
                try:
                    await asyncio.shield(reaper)
                    break
                except asyncio.CancelledError:
                    interrupted = True
            for task in tasks:
                if task is not reaper and not task.done():
                    task.cancel()
            if tasks:
                readers = [task for task in tasks if task is not reaper]
                reader_cleanup = asyncio.gather(*readers, return_exceptions=True)
                while True:
                    try:
                        await asyncio.shield(reader_cleanup)
                        break
                    except asyncio.CancelledError:
                        interrupted = True
            if interrupted:
                raise asyncio.CancelledError

    @staticmethod
    async def _read_bounded(stream: asyncio.StreamReader) -> bytes:
        result = bytearray()
        while True:
            chunk = await stream.read(min(65_536, MAX_OUTPUT_BYTES + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > MAX_OUTPUT_BYTES:
                raise PrivacyError("ANTIGRAVITY_METADATA_OUTPUT_LIMIT")

    @staticmethod
    def _bounded_string(value: object, *, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        return value

    @classmethod
    def parse_usage(cls, raw: bytes) -> list[dict[str, object]]:
        payload = strict_json(raw, code="ANTIGRAVITY_METADATA_INVALID")
        if not isinstance(payload, dict):
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        if set(payload) != {
            "conversation_id",
            "status",
            "response",
            "duration_seconds",
            "num_turns",
            "usage",
            "command",
        }:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        if (
            payload["conversation_id"] != ""
            or payload["status"] != "SUCCESS"
            or not isinstance(payload["response"], str)
            or len(payload["response"]) > MAX_OUTPUT_BYTES
            or type(payload["num_turns"]) is not int
            or payload["num_turns"] != 0
        ):
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        duration = payload["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
            or duration > PROCESS_TIMEOUT + 5
        ):
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        usage = payload["usage"]
        if not isinstance(usage, dict) or set(usage) != {
            "input_tokens",
            "output_tokens",
            "thinking_tokens",
            "cache_read_tokens",
            "total_tokens",
        }:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        if any(type(value) is not int or value != 0 for value in usage.values()):
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        command = payload["command"]
        if not isinstance(command, dict) or set(command) != {"name", "data"}:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        if command["name"] != "usage":
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        data = command["data"]
        if not isinstance(data, dict) or set(data) != {"description", "groups"}:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        cls._bounded_string(data["description"], maximum=2_000)
        groups = data["groups"]
        if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
            raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        quota: list[dict[str, object]] = []
        for group in groups:
            if not isinstance(group, dict) or set(group) != {"name", "description", "buckets"}:
                raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
            cls._bounded_string(group["name"], maximum=100)
            cls._bounded_string(group["description"], maximum=2_000)
            buckets = group["buckets"]
            if not isinstance(buckets, list) or not 1 <= len(buckets) <= MAX_BUCKETS:
                raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
            for bucket in buckets:
                required = {
                    "id",
                    "name",
                    "window",
                    "remaining_fraction",
                    "reset_time",
                }
                if (
                    not isinstance(bucket, dict)
                    or not required <= set(bucket)
                    or set(bucket) - required not in (set(), {"description"})
                ):
                    raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
                cls._bounded_string(bucket["id"], maximum=100)
                cls._bounded_string(bucket["name"], maximum=100)
                if "description" in bucket:
                    cls._bounded_string(bucket["description"], maximum=2_000)
                if bucket["id"] not in _KNOWN_GEMINI_BUCKETS:
                    continue
                window = cls._bounded_string(bucket["window"], maximum=32)
                if window != _KNOWN_GEMINI_BUCKETS[bucket["id"]]:
                    raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
                remaining = bucket["remaining_fraction"]
                if (
                    isinstance(remaining, bool)
                    or not isinstance(remaining, (int, float))
                    or not 0 <= remaining <= 1
                ):
                    raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
                reset_time = cls._bounded_string(bucket["reset_time"], maximum=40)
                if not _RESET_TIME.fullmatch(reset_time):
                    raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
                quota.append(
                    {
                        "group": "Gemini Models",
                        "window": _KNOWN_GEMINI_BUCKETS[bucket["id"]],
                        "remaining_fraction": float(remaining),
                        "reset_time": reset_time,
                    }
                )
                if len(quota) > MAX_QUOTA_BUCKETS:
                    raise PrivacyError("ANTIGRAVITY_METADATA_INVALID")
        return quota

    @staticmethod
    def model_in_catalog(raw: bytes) -> bool:
        if len(raw) > MAX_OUTPUT_BYTES:
            return False
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return bool(_MODEL_LINE.search(text))

    async def check(self) -> None:
        async with self.lock:
            self.authenticated = False
            self.model = ""
            self.quota = []
            self.status = "unchecked" if self.executable is not None else "unavailable"
            self.code = "" if self.executable is not None else "ANTIGRAVITY_NOT_FOUND"
            if self.executable is None:
                return
            try:
                await asyncio.to_thread(self._verify_binary)
                with tempfile.TemporaryDirectory(prefix="censitive-antigravity-metadata-") as raw:
                    workspace = Path(raw)
                    usage = await self._run(
                        ["-p", "/usage", "--output-format", "json", "--print-timeout", "10s"],
                        workspace,
                    )
                    quota = self.parse_usage(usage)
                    try:
                        models = await self._run(["models"], workspace)
                    except PrivacyError:
                        models = b""
            except asyncio.CancelledError:
                self.status, self.code = "unavailable", "CONNECTION_FAILED"
                raise
            except PrivacyError as error:
                code = str(error)
                self.status = "unavailable"
                self.code = (
                    "CONNECTION_FAILED" if code.startswith("ANTIGRAVITY_METADATA_") else code
                )
                return
            except OSError:
                self.status, self.code = "unavailable", "CONNECTION_FAILED"
                return
            self.authenticated = True
            self.model = MODEL_ID if self.model_in_catalog(models) else ""
            self.quota = quota
            self.status, self.code = "unavailable", "ANTIGRAVITY_BOUNDARY_UNVERIFIED"


__all__ = ["AntigravityConnection", "CLI_SHA256", "EXECUTABLE_ENV", "MODEL_ID"]
