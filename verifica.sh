#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6.1 — verifica dell'installazione.
# Ogni controllo è reale: esegue il comando e legge l'esito, non la presenza del file.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 1

OK=0
KO=0
WARN=0
REPORT=""

pass() { OK=$((OK + 1)); REPORT="${REPORT}OK   · $1\n"; }
fail() { KO=$((KO + 1)); REPORT="${REPORT}KO   · $1\n"; }
note() { WARN=$((WARN + 1)); REPORT="${REPORT}avv. · $1\n"; }

check() { # nome, comando
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$label"; else fail "$label"; fi
}

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "======================================================"
echo "  KORA STUDIO LIGHT 3.6.1 · Verifica installazione"
echo "======================================================"

# 1-8 · ambiente Python -------------------------------------------------------
if [ -x ".venv/bin/python" ]; then pass "Ambiente virtuale .venv presente"; else fail "Ambiente virtuale .venv assente"; fi
check "Python 3.9+ disponibile" "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)'
check "pydantic importabile" "$PY" -c 'import pydantic'
check "FastAPI importabile" "$PY" -c 'import fastapi'
check "Uvicorn importabile" "$PY" -c 'import uvicorn'
check "Pillow importabile" "$PY" -c 'import PIL'
check "NumPy importabile" "$PY" -c 'import numpy'
check "Requests importabile" "$PY" -c 'import requests'
check "python-multipart importabile" "$PY" -c 'import multipart'

# 9-14 · media ---------------------------------------------------------------
check "FFmpeg risponde (-version)" .venv/bin/ffmpeg -version
check "FFprobe risponde (-version)" .venv/bin/ffprobe -version
check "FFmpeg ha il filtro zoompan" bash -c '.venv/bin/ffmpeg -hide_banner -filters 2>/dev/null | grep -q zoompan'
check "FFmpeg ha libx264" bash -c '.venv/bin/ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264'
check "FFmpeg ha il filtro overlay" bash -c '.venv/bin/ffmpeg -hide_banner -filters 2>/dev/null | grep -q " overlay "'
if "$PY" -c 'import av' >/dev/null 2>&1; then pass "PyAV presente (fallback decodifica)"; else note "PyAV assente (solo avviso: serve se manca ffprobe)"; fi

# 15-18 · interfaccia -------------------------------------------------------
if [ -f "kora_studio/frontend/out/index.html" ]; then pass "Interfaccia compilata (index.html)"; else fail "Interfaccia NON compilata"; fi
check "Asset CSS/JS dell'interfaccia presenti" bash -c 'test -d kora_studio/frontend/out/_next'
check "Pagina 404 statica presente" test -f kora_studio/frontend/out/404.html
check "Bundle interfaccia non vuoto" bash -c 'test $(du -sk kora_studio/frontend/out | cut -f1) -gt 200'

# 19-23 · codice KORA -------------------------------------------------------
check "Pacchetto kora_studio importabile" "$PY" -c 'import kora_studio'
check "Agenti v3.5 importabili" "$PY" -c 'from kora_studio.agents import caption_designer, script_director, art_director, scene_generator'
check "API KORA costruibile" "$PY" -c 'from kora_studio.api.app import create_app; create_app(auto_seed=False)'
check "CLI KORA risponde" "$PY" -m kora_studio --help
check "Motore di movimento disponibile" "$PY" -c 'from kora_studio.agents.ai_media_lab import motion_expressions; assert motion_expressions("slow_push_in", 25)'

# 24-28 · modelli, QC, ripristino (v3.6) -------------------------------------
check "Model Manager importabile" "$PY" -c 'from kora_studio.ai_engine.model_manager import ModelManager, DEFAULT_CONFIG; assert DEFAULT_CONFIG["timeouts"]["ideation"] == 45'
check "Stati QC a quattro valori" "$PY" -c 'from kora_studio.db.models import QC_STATUSES; assert len(QC_STATUSES) == 4'
check "Blocchi regia includono motion_speed" bash -c 'grep -q "motion_speed" kora_studio/agents/scene_editor.py'
check "Script di ripristino eseguibile" test -x ripristina.sh
check "Master audio a −14 LUFS" bash -c 'grep -q "loudnorm=I=-14" kora_studio/agents/cinematic_editor.py'

# 29-32 · dati e identità ---------------------------------------------------
check "Logo originale KORA presente" bash -c 'ls assets/kora/logo/* >/dev/null 2>&1'
check "Font KORA presenti" bash -c 'test -f assets/kora/PlayfairDisplay.ttf && test -f assets/kora/SpaceGrotesk.ttf'
check "Database KORA apribile" "$PY" -c 'from kora_studio.db.database import Database; Database()'
check "Cartella progetti scrivibile" bash -c 'mkdir -p ~/.kora/projects && test -w ~/.kora/projects'

# 28-30 · spazio, porta, versione -------------------------------------------
FREE_MB=$(df -m "$ROOT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "${FREE_MB:-}" ] && [ "$FREE_MB" -gt 800 ]; then pass "Spazio disco sufficiente (${FREE_MB} MB liberi)"; else note "Spazio disco basso (${FREE_MB:-?} MB liberi): servono ~1 GB per i Reel"; fi
PORT="${KORA_PORT:-8020}"
if "$PY" - "$PORT" <<'PY' >/dev/null 2>&1
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", port))
finally:
    s.close()
PY
then pass "Porta $PORT libera"; else note "Porta $PORT già occupata: usa KORA_PORT=8021 ./start.sh"; fi
check "Versione 3.6 registrata" bash -c 'grep -q "version=\"3.6" kora_studio/api/app.py'
[ -x "diagnostica_ai.sh" ] && pass "Script diagnostica_ai.sh eseguibile" || fail "Script diagnostica_ai.sh mancante"

# ---------------------------------------------------------------- riepilogo
printf '\n------------------------------------------------------\n'
printf "%b" "$REPORT"
printf -- '------------------------------------------------------\n'
echo "esiti: $OK OK · $KO KO · $WARN avvisi"
echo

if [ "$KO" -gt 0 ]; then
  echo "Ci sono controlli falliti: rilancia «Installa KORA.command» e leggi i messaggi XX."
  exit 1
fi
if [ "$WARN" -gt 0 ]; then
  echo "Installazione valida con avvisi (vedi sopra)."
  exit 0
fi
echo "Installazione verificata: tutto pronto."
exit 0
