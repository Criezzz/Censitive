from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    device: str
    archive: str
    directory: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return (
            "https://github.com/ggml-org/llama.cpp/releases/download/b11063/"
            f"llama-b11063-bin-win-{self.device}-x64.zip"
        )


def runtime_profile(device: str) -> RuntimeProfile:
    if device == "cpu":
        return RuntimeProfile(
            "cpu",
            "llama-cpu.zip",
            "llama-b11063-cpu",
            "8c5dc1310d9c61953d8079ebe10ee156afa935837516fef3bcad29365ab12122",
            18_466_610,
        )
    if device == "vulkan":
        return RuntimeProfile(
            "vulkan",
            "llama-vulkan.zip",
            "llama-b11063",
            "417bb70dc3deeaef42328e1b1f2ba7a30db95401d2223b4fb41150f0d92b246b",
            31_851_331,
        )
    raise ValueError("UNSUPPORTED_LOCAL_DEVICE")
