import uvicorn

from privacy_gateway.adapters.connections import ProviderConnections
from privacy_gateway.adapters.local_censitive import LocalCensitiveMethod
from privacy_gateway.adapters.openai_provider import OpenAIProvider
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway


def main(key: str = "") -> None:
    provider = OpenAIProvider.from_environment()
    gateway = Gateway(provider, LocalCensitiveMethod(key=key))
    uvicorn.run(
        create_app(gateway, ProviderConnections(provider)),
        host="127.0.0.1",
        port=8765,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
