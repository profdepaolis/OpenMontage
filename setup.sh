#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6 — preparazione ambiente (idempotente, sicura da rilanciare).
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 1

FAIL=0
step() { printf '\n[%s] %s\n' "$1" "$2"; }
ok()   { printf '     OK  %s\n' "$1"; }
warn() { printf '     !!  %s\n' "$1"; }
bad()  { printf '     XX  %s\n' "$1"; FAIL=1; }

echo "======================================================"
echo "  KORA STUDIO LIGHT 3.6 · The Art of Being"
echo "  Preparazione dell'ambiente"
echo "======================================================"

# 1 ---------------------------------------------------------------- Python
step 1/7 "Controllo Python"
if ! command -v python3 >/dev/null 2>&1; then
  bad "python3 non trovato. Installa Python 3.11+ da https://www.python.org/downloads/"
  echo; echo "Preparazione interrotta."; exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 9) else 0)')"
if [ "$PY_OK" = "1" ]; then ok "Python $PY_VER"; else bad "Python $PY_VER troppo vecchio: serve 3.9 o superiore (consigliato 3.11)"; fi

# 2 ------------------------------------------------------------------ venv
step 2/7 "Ambiente virtuale .venv"
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv || { bad "Creazione .venv fallita"; exit 1; }
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || warn "Aggiornamento pip non riuscito (non bloccante)"
ok "Ambiente pronto: $("$ROOT_DIR/.venv/bin/python" -V 2>&1)"

# 3 ------------------------------------------------------------ dipendenze
step 3/7 "Dipendenze KORA"
if ! .venv/bin/pip install --prefer-binary -r kora_studio/requirements_kora.txt >/tmp/kora_pip.log 2>&1; then
  bad "Installazione dipendenze non riuscita. Dettagli: /tmp/kora_pip.log"
  tail -5 /tmp/kora_pip.log 2>/dev/null
else
  ok "Dipendenze core installate"
fi
# Extra utili ma non bloccanti (decodifica video, voce offline, download ffprobe)
.venv/bin/pip install --prefer-binary av >/dev/null 2>&1 && ok "PyAV presente (decodifica video)" || warn "PyAV non installato (solo avviso)"
.venv/bin/pip install --prefer-binary edge-tts >/dev/null 2>&1 && ok "Voce edge-tts presente" || warn "edge-tts non installato (solo avviso)"

# 3b ------------------------------------------- versione precedente e backup
step 3b/7 "Versione precedente e backup dei dati"
KORA_VERSION="3.6.8"
VERSION_FILE="kora_data/installed_version.txt"
if [ -f "kora_data/kora_studio.db" ]; then
  PREV=""
  [ -f "$VERSION_FILE" ] && PREV="$(tr -d '[:space:]' < "$VERSION_FILE")"
  if [ -n "$PREV" ] && [ "$PREV" != "$KORA_VERSION" ]; then
    ok "Installazione precedente rilevata: $PREV → aggiornamento a $KORA_VERSION"
  elif [ -z "$PREV" ]; then
    ok "Dati KORA esistenti (versione non registrata): aggiornamento conservativo"
  else
    ok "Stessa versione già installata: nessuna migrazione distruttiva"
  fi
  mkdir -p kora_data/backups
  STAMP="$(date +%Y%m%d-%H%M%S)"
  SAFETY="kora_data/backups/pre_aggiornamento_${STAMP}.db"
  if cp "kora_data/kora_studio.db" "$SAFETY" 2>/dev/null; then
    ok "Copia di sicurezza del database: $SAFETY"
  else
    warn "Copia di sicurezza non riuscita (spazio disco?): i dati restano intatti"
  fi
else
  ok "Prima installazione: nessun dato precedente da migrare"
fi
mkdir -p kora_data
printf '%s\n' "$KORA_VERSION" > "$VERSION_FILE"
ok "Versione $KORA_VERSION registrata in $VERSION_FILE"
ok "Architettura Mac rilevata: $(uname -m) ($(uname -s))"
if command -v opencode >/dev/null 2>&1; then
  ok "OpenCode già presente: non viene reinstallato né riconfigurato"
else
  ok "OpenCode assente: KORA parte comunque (rendering locale). Installalo solo se vuoi i modelli AI"
fi

# 4 ------------------------------------------------------- FFmpeg / FFprobe
step 4/7 "FFmpeg e FFprobe"
if ! .venv/bin/ffprobe -version >/dev/null 2>&1 && ! command -v ffprobe >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    echo "     ..  FFprobe non presente: provo a scaricarlo in locale (nessuna installazione globale)"
    mkdir -p .runtime/ffprobe
    npm install --prefix .runtime/ffprobe ffprobe-static --no-audit --no-fund >/dev/null 2>&1 || true
  fi
fi

MEDIA_STATUS="$(.venv/bin/python - <<'PY'
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

root = Path.cwd()
bin_dir = root / ".venv" / "bin"
bin_dir.mkdir(parents=True, exist_ok=True)


def works(path) -> bool:
    try:
        proc = subprocess.run([str(path), "-version"], capture_output=True, timeout=25)
        return proc.returncode == 0 and b"version" in (proc.stdout or b"").lower()
    except Exception:
        return False


def link(target: Path, name: str) -> bool:
    dest = bin_dir / name
    try:
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(target)
        os.chmod(dest, 0o755)
        return works(dest)
    except Exception:
        return False


system, machine = platform.system().lower(), platform.machine().lower()
want = {
    "linux": "linux-x64" if machine in ("x86_64", "amd64") else "linux-arm64",
    "darwin": "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64",
    "windows": "win32-x64",
}.get(system, "")

# --- ffmpeg
ffmpeg_ok = (bin_dir / "ffmpeg").exists() and works(bin_dir / "ffmpeg")
if not ffmpeg_ok and shutil.which("ffmpeg") and works(shutil.which("ffmpeg")):
    ffmpeg_ok = link(Path(shutil.which("ffmpeg")), "ffmpeg")
if not ffmpeg_ok:
    try:
        import imageio_ffmpeg

        ffmpeg_ok = link(Path(imageio_ffmpeg.get_ffmpeg_exe()), "ffmpeg")
    except Exception:
        pass
print(("FFMPEG_OK" if ffmpeg_ok else "FFMPEG_MISSING"))

# --- ffprobe: mai linkare un binario di un'altra piattaforma (Exec format error)
ffprobe_ok = (bin_dir / "ffprobe").exists() and works(bin_dir / "ffprobe")
if not ffprobe_ok and shutil.which("ffprobe") and works(shutil.which("ffprobe")):
    ffprobe_ok = link(Path(shutil.which("ffprobe")), "ffprobe")

if not ffprobe_ok:
    # ffprobe-static include i binari di tutte le piattaforme: si sceglie solo quello
    # che risponde davvero a `-version` (un binario di un'altra architettura fallisce).
    for base in (root / ".runtime" / "ffprobe", Path.home() / ".npm-global", root / "node_modules"):
        if not base.exists():
            continue
        candidates = [
            c for c in base.rglob("ffprobe*")
            if c.is_file() and not c.name.endswith((".js", ".json", ".md", ".ts"))
            and "win32" not in str(c).lower().replace("\\", "/")
        ]
        candidates.sort(key=lambda c: (want not in str(c).lower().replace("/", "-"), len(str(c))))
        for cand in candidates:
            if works(cand) and link(cand, "ffprobe"):
                ffprobe_ok = True
                break
        if ffprobe_ok:
            break

if not ffprobe_ok:
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
        exe = shutil.which("ffprobe")
        if exe and works(exe):
            ffprobe_ok = link(Path(exe), "ffprobe")
    except Exception:
        pass

print("FFPROBE_OK" if ffprobe_ok else "FFPROBE_MISSING")
PY
)"

case "$MEDIA_STATUS" in
  *FFMPEG_OK*)  ok "FFmpeg pronto e funzionante" ;;
  *)            bad "FFmpeg non disponibile: su macOS esegui 'brew install ffmpeg'" ;;
esac
case "$MEDIA_STATUS" in
  *FFPROBE_OK*) ok "FFprobe pronto e funzionante" ;;
  *)            warn "FFprobe assente: il conteggio frame userà PyAV (se installato). Per la massima precisione: 'brew install ffmpeg'" ;;
esac

# 5 ---------------------------------------------------------------- frontend
step 5/7 "Interfaccia web"
if [ -f "kora_studio/frontend/out/index.html" ]; then
  ok "Interfaccia già compilata (kora_studio/frontend/out)"
elif command -v npm >/dev/null 2>&1; then
  echo "     ..  compilazione interfaccia (può richiedere qualche minuto)"
  if ( cd kora_studio/frontend && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build ) >/tmp/kora_frontend.log 2>&1; then
    ok "Interfaccia compilata"
  else
    bad "Compilazione interfaccia fallita. Dettagli: /tmp/kora_frontend.log"
  fi
else
  bad "Interfaccia non compilata e npm assente: reinstalla il pacchetto KORA completo"
fi

# 6 --------------------------------------------------------- materiali + DB
step 6/7 "Materiali autentici KORA e diagnostica"
if .venv/bin/python -m kora_studio seed >/tmp/kora_seed.log 2>&1; then
  ok "Brand Brain, Media Vault e Sound Library inizializzati"
else
  warn "Import materiali incompleto (vedi /tmp/kora_seed.log): KORA può partire lo stesso"
  tail -3 /tmp/kora_seed.log 2>/dev/null
fi
if .venv/bin/python -m kora_studio doctor >/tmp/kora_doctor.log 2>&1; then
  ok "Diagnostica completata (dettagli in /tmp/kora_doctor.log)"
else
  warn "Diagnostica con avvisi (dettagli in /tmp/kora_doctor.log)"
fi

# 7 ------------------------------------------------------------------- fine
step 7/7 "Esito"
if [ "$FAIL" = "0" ]; then
  echo
  echo "======================================================"
  echo "  Setup completato!"
  echo "  Ora puoi fare doppio clic su «Avvia KORA.command»"
  echo "======================================================"
  exit 0
fi

echo
echo "======================================================"
echo "  Setup NON completato: leggi i messaggi XX qui sopra."
echo "  Puoi rilanciare questo file quando vuoi: riparte da dove serve."
echo "======================================================"
exit 1
