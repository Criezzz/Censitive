$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $needsSetup = -not (Test-Path -LiteralPath '.venv/Scripts/python.exe') -or -not (Test-Path -LiteralPath 'backend/privacy_gateway/web/index.html') -or -not (Test-Path -LiteralPath '.local/models/Qwen3-1.7B-Q4_K_M.gguf') -or -not (Test-Path -LiteralPath '.local/runtime/llama-b11063-cpu/llama-server.exe')
    if ($needsSetup) {
        uv sync --locked
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
        try {
            npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
        uv run --locked python tool/setup_semantic.py
        if ($LASTEXITCODE -ne 0) { throw 'Local model installation failed.' }
    }
    Write-Host 'Censitive: http://127.0.0.1:8765 (Ctrl+C to stop)'
    uv run --locked python tool/serve_semantic.py
    if ($LASTEXITCODE -ne 0) { throw 'Demo server exited with an error.' }
} finally { Pop-Location }
