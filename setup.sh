#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "=== KORA STUDIO · Setup & Diagnostica ==="

# 1. Check Python 3.11+
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERRORE: python3 non trovato. Installa Python 3.11+."
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[OK] Python $PY_VER rilevato"

# 2. Create .venv if absent
if [ ! -d ".venv" ]; then
  echo "[..] Creazione ambiente virtuale .venv..."
  python3 -m venv .venv
fi

# 3. Install core KORA STUDIO Python dependencies (prebuilt wheels, no Rust compiler needed)
echo "[..] Installazione/verifica dipendenze Python core KORA STUDIO..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install --prefer-binary -r kora_studio/requirements_kora.txt >/dev/null

# Optional extras (best-effort, never block setup on older macOS/Intel)
.venv/bin/pip install --prefer-binary av piper-tts gTTS >/dev/null 2>&1 || true

# 4. Ensure FFmpeg & FFprobe available in .venv/bin
if [ ! -x ".venv/bin/ffmpeg" ]; then
  FFMPEG_PATH="$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
  ln -sf "$FFMPEG_PATH" .venv/bin/ffmpeg
fi
if [ ! -x ".venv/bin/ffprobe" ]; then
  if command -v ffprobe >/dev/null 2>&1; then
    ln -sf "$(command -v ffprobe)" .venv/bin/ffprobe
  elif command -v npm >/dev/null 2>&1; then
    echo "[..] Installazione ffprobe-static locale..."
    mkdir -p .runtime/ffprobe
    npm install --prefix .runtime/ffprobe ffprobe-static --no-audit --no-fund >/dev/null 2>&1 || true
    FFPROBE_BIN="$(find .runtime/ffprobe -name ffprobe -type f 2>/dev/null | head -n 1 || true)"
    if [ -n "$FFPROBE_BIN" ]; then
      chmod +x "$FFPROBE_BIN"
      ln -sf "$ROOT_DIR/$FFPROBE_BIN" .venv/bin/ffprobe 2>/dev/null || ln -sf "$FFPROBE_BIN" .venv/bin/ffprobe
    fi
  fi
fi

# 5. Build Frontend Static Bundle & Remotion Composer if Node/npm is present
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if [ ! -f "kora_studio/frontend/out/index.html" ]; then
    echo "[..] Build frontend KORA STUDIO..."
    (
      cd kora_studio/frontend
      if [ ! -d "node_modules" ]; then
        npm install --no-audit --no-fund
      fi
      npm run build
    )
  else
    echo "[OK] Frontend statico KORA STUDIO già pronto."
  fi
  if [ -d "remotion-composer" ]; then
    echo "[..] Preparazione Remotion Composer locale..."
    (
      cd remotion-composer
      if [ ! -d "node_modules" ]; then
        npm install --no-audit --no-fund >/dev/null 2>&1 || true
      fi
      if [ -x "./node_modules/.bin/remotion" ]; then
        ./node_modules/.bin/remotion browser ensure >/dev/null 2>&1 || echo "[INFO] Browser Remotion non scaricato (o Chrome già presente): FFmpeg resterà il motore principale."
      fi
    )
  fi
else
  echo "[OK] Node/npm non richiesto: uso il bundle statico già incluso in kora_studio/frontend/out."
fi

# 6. Seed KORA assets & run diagnostics
echo "[..] Caricamento materiali autentici KORA e diagnostica..."
.venv/bin/python -m kora_studio seed
.venv/bin/python -m kora_studio doctor

echo ""
echo "Setup completato! Avvia KORA STUDIO con: ./start.sh"
