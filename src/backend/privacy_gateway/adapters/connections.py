import asyncio
import subprocess
import tempfile
from pathlib import Path

from privacy_gateway.adapters.codex_provider import CodexProvider
from privacy_gateway.adapters.openai_provider import OpenAIProvider
from privacy_gateway.domain.redaction import PrivacyError


class ProviderConnections:
    def __init__(self, openai: OpenAIProvider) -> None:
        self.openai = openai
        self.codex = None
        self.status = "unchecked"
        self.code = ""
        self.lock = asyncio.Lock()
        self.login_task = None
        self.login_owner = ""
        self.models: list[str] = []

    def catalog(self) -> list[dict[str, object]]:
        code = self.openai.configuration_error()
        return [
            {
                "id": "openai-api",
                "name": "OpenAI API",
                "model": self.openai.model,
                "models": self.models or [self.openai.model],
                "auth": "api-key",
                "status": "unavailable" if code else "configured",
                "code": code,
            },
            {
                "id": "codex-chatgpt",
                "name": "Codex",
                "model": self.codex.model if self.codex else CodexProvider.model,
                "models": self.codex.models if self.codex else [],
                "auth": "chatgpt-cli",
                "status": self.status,
                "code": self.code,
            },
        ]

    async def refresh_models(self) -> None:
        if self.models or self.openai.configuration_error():
            return
        try:
            self.models = list(dict.fromkeys([self.openai.model, *await self.openai.list_models()]))
        except PrivacyError:
            pass

    def _load(self) -> CodexProvider:
        if self.codex is None:
            self.codex = CodexProvider()
        return self.codex

    async def check(self) -> None:
        async with self.lock:
            if self.login_task is not None and not self.login_task.done():
                return
            try:
                provider = await asyncio.to_thread(self._load)
                await asyncio.to_thread(provider.check_login)
            except (PrivacyError, OSError, subprocess.SubprocessError, ValueError) as error:
                self.code = str(error) if isinstance(error, PrivacyError) else "CONNECTION_FAILED"
                self.status = (
                    "needs_login" if self.code == "CODEX_CHATGPT_LOGIN_REQUIRED" else "unavailable"
                )
            else:
                self.status, self.code = "ready", ""

    async def resolve(self, provider_id: str, model: str) -> OpenAIProvider | CodexProvider:
        if provider_id == "openai-api":
            code = self.openai.configuration_error()
            if code:
                raise PrivacyError(code)
            await self.refresh_models()
            if model not in (self.models or [self.openai.model]):
                raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
            return self.openai.with_model(model)
        if provider_id != "codex-chatgpt":
            raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
        await self.check()
        if self.status != "ready" or self.codex is None:
            raise PrivacyError(self.code or "LOGIN_BUSY")
        if model not in self.codex.models:
            raise PrivacyError("UNKNOWN_PROVIDER_MODEL")
        if model == self.codex.model:
            return self.codex
        return await asyncio.to_thread(CodexProvider, self.codex.executable, model=model)

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
            parent = Path(__file__).resolve().parents[3] / ".local/runtime"
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="censitive-login-", dir=parent) as directory:
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
