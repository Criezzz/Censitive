import os
import secrets
import subprocess
import tempfile
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path

import httpx

from privacy_gateway.__main__ import main as serve
from privacy_gateway.settings import CONTEXT, MODEL, MODEL_FILE, RUNTIME_DIRECTORY

ROOT = Path(__file__).resolve().parents[1]


def stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    for child in reversed(children):
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


@contextmanager
def local_runtime():
    binary = ROOT / ".local/runtime" / RUNTIME_DIRECTORY / "llama-server.exe"
    model = ROOT / ".local/models" / MODEL_FILE
    if not binary.is_file() or not model.is_file():
        raise RuntimeError("Chưa cài xong Qwen. Chạy lại demo.cmd.")
    scratch = ROOT / ".local/runtime"
    with tempfile.TemporaryDirectory(prefix="censitive-runtime-", dir=scratch) as directory:
        key = secrets.token_hex(32)
        keyfile = Path(directory) / "local.key"
        keyfile.write_text(key, encoding="utf-8")
        command = [
            str(binary),
            "-m",
            str(model),
            "--alias",
            MODEL,
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
            "-ngl",
            "0",
            "-c",
            str(CONTEXT),
            "-np",
            "1",
            "-b",
            "256",
            "-ub",
            "128",
            "-t",
            "4",
            "-ctk",
            "q8_0",
            "-ctv",
            "q8_0",
            "-fa",
            "on",
            "--reasoning",
            "off",
            "--cache-ram",
            "0",
            "--no-context-shift",
            "--no-webui",
            "--no-webui-mcp-proxy",
            "--no-agent",
            "--no-slots",
            "--cors-origins",
            "http://127.0.0.1:8011",
            "--no-cors-credentials",
            "--api-key-file",
            str(keyfile),
            "--log-disable",
            "--device",
            "none",
            "--no-kv-offload",
            "--no-op-offload",
        ]
        with httpx.Client(trust_env=False, timeout=3) as client:
            try:
                client.get("http://127.0.0.1:8011/health")
            except httpx.HTTPError:
                pass
            else:
                raise RuntimeError("Cổng Qwen đang được sử dụng.")
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper() != "OPENAI_API_KEY" and not k.upper().startswith(("LLAMA_", "GGML_"))
            }
            process = subprocess.Popen(
                command,
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        response = client.get(
                            "http://127.0.0.1:8011/v1/models",
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        if response.status_code == 200 and any(
                            item.get("id") == MODEL for item in response.json().get("data", [])
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Chưa khởi động được Qwen.")
                yield key
            finally:
                stop_children([process])


def open_browser() -> None:
    with httpx.Client(trust_env=False, timeout=2) as client:
        while True:
            try:
                if client.get("http://127.0.0.1:8765/").status_code == 200:
                    webbrowser.open("http://127.0.0.1:8765")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)


def main() -> None:
    try:
        with local_runtime() as key:
            threading.Thread(target=open_browser, daemon=True).start()
            serve(key)
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        print(str(error), flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
