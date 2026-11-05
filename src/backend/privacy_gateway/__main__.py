import argparse
import asyncio
from pathlib import Path

import uvicorn

from privacy_gateway.adapters.codex_provider import CodexProvider
from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.adapters.conversation_store import (
    ConversationStoreError,
    EncryptedConversationStore,
)
from privacy_gateway.adapters.local_chat import LocalChatProvider
from privacy_gateway.adapters.local_detector import LocalDetector
from privacy_gateway.adapters.local_private_worker import LocalPrivateWorker
from privacy_gateway.adapters.openai_provider import OpenAIProvider
from privacy_gateway.adapters.state_resolver import LocalStateResolver
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.application.session_records import EncryptedSessionRepository
from privacy_gateway.domain.redaction import PrivacyError


def main() -> None:
    parser = argparse.ArgumentParser(description="Censitive privacy gateway")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--provider", choices=("demo", "codex", "openai", "local"), default="openai"
    )
    parser.add_argument("--detector", choices=("rules", "qwen"), default="rules")
    parser.add_argument("--local-model", choices=("1.7b", "4b", "8b"), default="8b")
    parser.add_argument("--semantic-mode", choices=("literal", "state"), default="literal")
    parser.add_argument(
        "--storage-dir",
        type=Path,
        help="Save encrypted conversations locally (Windows DPAPI key; omit for memory-only).",
    )
    args = parser.parse_args()
    if args.semantic_mode == "state" and args.detector != "qwen":
        parser.error("--semantic-mode state requires --detector qwen")
    if args.provider == "local" and args.detector != "qwen":
        parser.error("--provider local requires --detector qwen and its authenticated runtime")
    gateway = Gateway()
    openai = OpenAIProvider.from_environment()
    if args.provider == "openai":
        gateway = Gateway(openai)
    local = None
    if args.provider == "codex":
        try:
            provider = CodexProvider()
            provider.check_login()
        except (PrivacyError, OSError) as error:
            code = str(error) if isinstance(error, PrivacyError) else "STARTUP_FAILED"
            parser.exit(2, f"Codex unavailable: {code}\n")
        gateway = Gateway(provider)
    if args.detector == "qwen":
        runtime_lock = asyncio.Lock()
        local = LocalChatProvider(args.local_model, lock=runtime_lock)
        if args.provider == "local":
            if not local.key:
                parser.exit(2, "Local chat unavailable: LOCAL_CHAT_UNAVAILABLE\n")
            gateway = Gateway(local)
        if args.semantic_mode == "state":
            gateway.resolver = LocalStateResolver(args.local_model, lock=runtime_lock)
        else:
            gateway.detector = LocalDetector(args.local_model, lock=runtime_lock)
        worker = LocalPrivateWorker(args.local_model, lock=runtime_lock)
        if worker.key:
            gateway.private_worker = worker
    store = None
    try:
        if args.storage_dir is not None:
            store = EncryptedConversationStore(args.storage_dir / "conversations.sqlite")
            gateway.store = EncryptedSessionRepository(store)
        uvicorn.run(
            create_app(
                gateway,
                port=args.port,
                connections=ProviderConnections(gateway.provider, local=local, openai=openai),
                conversation_store=store,
            ),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            log_level="warning",
        )
    except (ConversationStoreError, PrivacyError):
        parser.exit(2, "Conversation storage unavailable; startup stopped.\n")
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
