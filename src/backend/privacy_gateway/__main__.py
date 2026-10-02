import argparse
import asyncio

import uvicorn

from privacy_gateway.adapters.codex_provider import CodexProvider
from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.adapters.local_chat import LocalChatProvider
from privacy_gateway.adapters.local_detector import LocalDetector
from privacy_gateway.adapters.state_resolver import LocalStateResolver
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError


def main() -> None:
    parser = argparse.ArgumentParser(description="Censitive privacy gateway")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--provider", choices=("demo", "codex"), default="demo")
    parser.add_argument("--detector", choices=("rules", "qwen"), default="rules")
    parser.add_argument("--local-model", choices=("4b", "8b"), default="8b")
    parser.add_argument("--semantic-mode", choices=("literal", "state"), default="literal")
    args = parser.parse_args()
    if args.semantic_mode == "state" and args.detector != "qwen":
        parser.error("--semantic-mode state requires --detector qwen")
    gateway = Gateway()
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
        if args.semantic_mode == "state":
            gateway.resolver = LocalStateResolver(args.local_model, lock=runtime_lock)
        else:
            gateway.detector = LocalDetector(args.local_model, lock=runtime_lock)
    uvicorn.run(
        create_app(
            gateway, port=args.port, connections=ProviderConnections(gateway.provider, local=local)
        ),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
