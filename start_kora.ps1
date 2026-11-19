$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creazione ambiente virtuale .venv..."
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\pip.exe install -r requirements.txt imageio-ffmpeg av piper-tts edge-tts gTTS fonttools brotli pytest httpx python-multipart
}

if (-not (Test-Path "kora_studio\frontend\out\index.html")) {
    Push-Location "kora_studio\frontend"
    if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
    npm run build
    Pop-Location
}

.venv\Scripts\python.exe -m kora_studio seed
.venv\Scripts\python.exe -m kora_studio doctor
.venv\Scripts\python.exe -m kora_studio serve --host 0.0.0.0 --port 8000
