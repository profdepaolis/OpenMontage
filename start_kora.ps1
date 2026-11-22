$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creazione ambiente virtuale .venv..."
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\pip.exe install --prefer-binary -r kora_studio\requirements_kora.txt
}

if (-not (Test-Path "kora_studio\frontend\out\index.html")) {
    Push-Location "kora_studio\frontend"
    if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
    npm run build
    Pop-Location
}

if (-not (Test-Path "remotion-composer\node_modules")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location "remotion-composer"
        npm install --no-audit --no-fund
        npx remotion browser ensure
        Pop-Location
    }
}

.venv\Scripts\python.exe -m kora_studio seed
.venv\Scripts\python.exe -m kora_studio doctor
.venv\Scripts\python.exe -m kora_studio serve --host 127.0.0.1 --port 8000
