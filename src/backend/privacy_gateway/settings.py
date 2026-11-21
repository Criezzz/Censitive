MODEL = "qwen3-1.7b-local"
MODEL_FILE = "Qwen3-1.7B-Q4_K_M.gguf"
MODEL_URL = "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/daeb8e2d528a760970442092f6bf1e55c3b659eb/Qwen3-1.7B-Q4_K_M.gguf"
MODEL_SHA256 = "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5"
RUNTIME_ARCHIVE = "llama-cpu.zip"
RUNTIME_DIRECTORY = "llama-b11063-cpu"
RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11063/llama-b11063-bin-win-cpu-x64.zip"
RUNTIME_SHA256 = "8c5dc1310d9c61953d8079ebe10ee156afa935837516fef3bcad29365ab12122"
LOCAL_ENDPOINT = "http://127.0.0.1:8011"
PROSE_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0, "seed": 17}
CHECK_SAMPLING = {"temperature": 0, "top_p": 0.95, "top_k": 20, "min_p": 0, "seed": 17}
PROSE_OPTIONS = {
    **PROSE_SAMPLING,
    "stream": False,
    "cache_prompt": False,
    "chat_template_kwargs": {"enable_thinking": True},
    "reasoning_format": "deepseek",
}
CHECK_OPTIONS = {
    **CHECK_SAMPLING,
    "stream": False,
    "cache_prompt": False,
    "chat_template_kwargs": {"enable_thinking": False},
}
CONTEXT = 16384
