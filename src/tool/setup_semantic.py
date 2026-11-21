import hashlib
import zipfile
from pathlib import Path

import httpx
from privacy_gateway.settings import (
    MODEL_URL,
    MODEL_FILE,
    MODEL_SHA256,
    RUNTIME_URL,
    RUNTIME_ARCHIVE,
    RUNTIME_DIRECTORY,
    RUNTIME_SHA256,
)

ROOT = Path(__file__).resolve().parents[1] / ".local"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(url: str, target: Path, expected: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        partial = target.with_suffix(target.suffix + ".partial")
        with httpx.Client(follow_redirects=True, trust_env=False, timeout=90) as client:
            with client.stream("GET", url) as response, partial.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    output.write(chunk)
        if digest(partial) != expected:
            raise RuntimeError("ARTIFACT_HASH_MISMATCH")
        partial.replace(target)
    print(f"Ready {target.name}", flush=True)


def extract_runtime(archive: Path, directory: Path) -> None:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            if not (directory / member.filename).resolve().is_relative_to(directory):
                raise RuntimeError("ARCHIVE_PATH_INVALID")
        zipped.extractall(directory)


def main() -> int:
    download(MODEL_URL, ROOT / "models" / MODEL_FILE, MODEL_SHA256)
    directory = ROOT / "runtime" / RUNTIME_DIRECTORY
    if not (directory / "llama-server.exe").is_file():
        archive = ROOT / "runtime" / RUNTIME_ARCHIVE
        download(RUNTIME_URL, archive, RUNTIME_SHA256)
        extract_runtime(archive, directory)
    print("Qwen Apache-2.0; llama.cpp MIT.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
