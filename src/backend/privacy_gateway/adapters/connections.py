import asyncio
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from privacy_gateway.adapters.antigravity_connection import AntigravityConnection
from privacy_gateway.adapters.codex_provider import CodexProvider
from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.adapters.local_chat import LocalChatProvider
from privacy_gateway.adapters.openai_provider import OpenAIProvider
from privacy_gateway.adapters.provider import Provider
from privacy_gateway.application.private_work import PrivateWorkbench, PrivateWorker
from privacy_gateway.domain.private_plan import planner_output_schema
from privacy_gateway.domain.redaction import PrivacyError

PUBLIC_CODES = {
    "CODEX_NOT_FOUND",
    "CODEX_VERSION_UNVERIFIED",
    "CODEX_CHATGPT_LOGIN_REQUIRED",
    "CODEX_LOGIN_FAILED",
    "CODEX_LOGIN_TIMEOUT",
    "CODEX_LOGIN_CANCELLED",
}


class ProviderConnections:
    def __init__(
        self,
        default: Provider,
        codex_factory: Callable[[], CodexProvider] = CodexProvider,
        *,
        local: LocalChatProvider | None = None,
        openai: OpenAIProvider | None = None,
        antigravity: AntigravityConnection | None = None,
    ) -> None:
        self.demo: Provider = default if default.provider_id == "local-demo" else FakeProvider()
        self.codex = default if isinstance(default, CodexProvider) else None
        self.factory = codex_factory
        self.status = "unchecked"
        self.code = ""
        self.lock = asyncio.Lock()
        self.login_task: asyncio.Task[None] | None = None
        self.login_owner = ""
        self.local = local
        self.openai = default if isinstance(default, OpenAIProvider) else openai
        self.antigravity = antigravity or AntigravityConnection()

    def catalog(self) -> list[dict[str, object]]:
        catalog: list[dict[str, object]] = [
            {
                "id": "local-demo",
                "name": "Demo local",
                "model": self.demo.model,
                "auth": "none",
                "status": "ready",
                "code": "",
            },
            {
                "id": "codex-chatgpt",
                "name": "Codex",
                "model": CodexProvider.model,
                "auth": "chatgpt-cli",
                "status": self.status,
                "code": self.code,
            },
        ]
        if self.local is not None:
            catalog.append(
                {
                    "id": self.local.provider_id,
                    "name": "Qwen local",
                    "model": self.local.model,
                    "auth": "local-runtime",
                    "status": "ready" if self.local.key else "unavailable",
                    "code": "" if self.local.key else "LOCAL_CHAT_UNAVAILABLE",
                    "streaming": True,
                }
            )
        catalog.append(self.antigravity.catalog())
        code = self.openai.configuration_error() if self.openai else "OPENAI_API_KEY_REQUIRED"
        catalog.append(
            {
                "id": "openai-api",
                "name": "OpenAI API",
                "model": self.openai.model if self.openai else "",
                "auth": "api-key",
                "status": "unavailable" if code else "configured",
                "code": code,
                "streaming": False,
            }
        )
        return catalog

    def _load(self) -> CodexProvider:
        if self.codex is None:
            self.codex = self.factory()
        self.codex._verify_binary()
        return self.codex

    async def check(self) -> None:
        async with self.lock:
            if self.login_task is not None and not self.login_task.done():
                return
            try:
                provider = await asyncio.to_thread(self._load)
                await asyncio.to_thread(provider.check_login)
            except (
                PrivacyError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
                StopIteration,
            ) as error:
                self.code = str(error) if str(error) in PUBLIC_CODES else "CONNECTION_FAILED"
                self.status = (
                    "needs_login" if self.code == "CODEX_CHATGPT_LOGIN_REQUIRED" else "unavailable"
                )
            else:
                self.status, self.code = "ready", ""

    async def check_antigravity(self) -> None:
        await self.antigravity.check()

    async def resolve(self, provider_id: str, model: str) -> Provider:
        if provider_id == "openai-api":
            if self.openai is None:
                raise PrivacyError("OPENAI_API_KEY_REQUIRED")
            code = self.openai.configuration_error()
            if code:
                raise PrivacyError(code)
            if model != self.openai.model:
                raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
            return self.openai
        if (
            self.local is not None
            and provider_id == self.local.provider_id
            and model == self.local.model
        ):
            if not self.local.key:
                raise PrivacyError("LOCAL_CHAT_UNAVAILABLE")
            return self.local
        if provider_id == "local-demo" and model == self.demo.model:
            return self.demo
        if provider_id == self.antigravity.provider_id:
            raise PrivacyError("ANTIGRAVITY_BOUNDARY_UNVERIFIED")
        if provider_id != "codex-chatgpt" or model != CodexProvider.model:
            raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
        await self.check()
        if self.status != "ready" or self.codex is None:
            raise PrivacyError(self.code or "LOGIN_BUSY")
        return self.codex

    def private_work_available(self, provider: Provider) -> bool:
        return (isinstance(provider, OpenAIProvider) and not provider.configuration_error()) or (
            isinstance(provider, CodexProvider) and provider.model == CodexProvider.model
        )

    async def private_work(
        self, provider: Provider, worker: PrivateWorker | None
    ) -> PrivateWorkbench:
        if worker is None or not self.private_work_available(provider):
            raise PrivacyError("PRIVATE_WORK_UNAVAILABLE")
        if isinstance(provider, OpenAIProvider):
            return PrivateWorkbench(provider.private_planner(), worker)
        assert isinstance(provider, CodexProvider)
        try:
            planner = await asyncio.to_thread(
                CodexProvider, provider.executable, output_schema=planner_output_schema()
            )
        except PrivacyError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
            raise PrivacyError("CONNECTION_FAILED") from None
        return PrivateWorkbench(planner, worker)

    async def start_login(self, owner: str) -> None:
        await self.check()
        async with self.lock:
            if self.login_task is not None and not self.login_task.done():
                if owner != self.login_owner:
                    raise PrivacyError("LOGIN_BUSY")
                return
            if self.status == "ready":
                return
            if self.status != "needs_login" or self.codex is None:
                raise PrivacyError(self.code or "CONNECTION_FAILED")
            self.login_owner = owner
            self.status, self.code = "signing_in", ""
            self.login_task = asyncio.create_task(self._login(self.codex))

    async def _login(self, provider: CodexProvider) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="censitive-login-") as directory:
                process = await asyncio.create_subprocess_exec(
                    provider.executable,
                    "login",
                    "-c",
                    'forced_login_method="chatgpt"',
                    cwd=Path(directory),
                    env=provider.env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    if await asyncio.wait_for(process.wait(), timeout=300):
                        raise PrivacyError("CODEX_LOGIN_FAILED")
                    await asyncio.to_thread(provider.check_login)
                    self.status, self.code = "ready", ""
                finally:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    reaper = asyncio.create_task(process.wait())
                    while True:
                        try:
                            await asyncio.shield(reaper)
                            break
                        except asyncio.CancelledError:
                            continue
        except asyncio.CancelledError:
            self.status, self.code = "needs_login", "CODEX_LOGIN_CANCELLED"
        except TimeoutError:
            self.status, self.code = "needs_login", "CODEX_LOGIN_TIMEOUT"
        except Exception:
            self.status, self.code = "needs_login", "CODEX_LOGIN_FAILED"

    async def cancel_login(self, owner: str) -> None:
        async with self.lock:
            if self.login_task is None or self.login_task.done():
                return
            if owner != self.login_owner:
                raise PrivacyError("LOGIN_BUSY")
            self.login_task.cancel()
            try:
                await self.login_task
            except asyncio.CancelledError:
                self.status, self.code = "needs_login", "CODEX_LOGIN_CANCELLED"

    async def close(self) -> None:
        if self.login_task is not None and not self.login_task.done():
            await self.cancel_login(self.login_owner)
