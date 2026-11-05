from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model_id: str
    filename: str
    sha256: str
    size: int
    repository: str
    revision: str
    gpu_layers: int

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.filename}"


PROFILES = MappingProxyType(
    {
        "1.7b": ModelProfile(
            "1.7b",
            "qwen3-1.7b-local",
            "Qwen3-1.7B-Q4_K_M.gguf",
            "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5",
            1_282_439_264,
            "ggml-org/Qwen3-1.7B-GGUF",
            "daeb8e2d528a760970442092f6bf1e55c3b659eb",
            24,
        ),
        "4b": ModelProfile(
            "4b",
            "qwen3-4b-local",
            "Qwen3-4B-Q4_K_M.gguf",
            "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
            2_497_280_256,
            "Qwen/Qwen3-4B-GGUF",
            "bc640142c66e1fdd12af0bd68f40445458f3869b",
            99,
        ),
        "8b": ModelProfile(
            "8b",
            "qwen3-8b-local",
            "Qwen3-8B-Q4_K_M.gguf",
            "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
            5_027_783_488,
            "Qwen/Qwen3-8B-GGUF",
            "7c41481f57cb95916b40956ab2f0b139b296d974",
            28,
        ),
    }
)


def model_profile(name: str) -> ModelProfile:
    if name not in PROFILES:
        raise ValueError("UNSUPPORTED_LOCAL_MODEL")
    return PROFILES[name]
