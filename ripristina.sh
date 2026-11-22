#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6 — ripristino di un backup, reversibile.
# Uso:
#   ./ripristina.sh                 elenca i backup disponibili
#   ./ripristina.sh --ultimo        ripristina il backup più recente (chiede conferma)
#   ./ripristina.sh FILE.zip --si   ripristina senza domanda (uso esperto)
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ ! -d "kora_data/backups" ]; then
  echo "Nessuna cartella di backup (kora_data/backups)."
  exit 1
fi

TARGET=""
CONFIRM="no"
for arg in "$@"; do
  case "$arg" in
    --ultimo) TARGET="__LATEST__" ;;
    --si) CONFIRM="si" ;;
    -*) echo "Opzione non riconosciuta: $arg"; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

echo "KORA — backup disponibili:"
ls -1t kora_data/backups/kora_studio_backup_*.zip 2>/dev/null | head -10 || true

if [ -z "$TARGET" ]; then
  echo ""
  echo "Per ripristinare: ./ripristina.sh --ultimo   oppure   ./ripristina.sh <file.zip>"
  exit 0
fi

PORT="${KORA_PORT:-8020}"
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  echo ""
  echo "ATTENZIONE: KORA sembra attivo sulla porta $PORT."
  echo "Chiudi l'app (o premi Ctrl+C nella finestra del server) prima di ripristinare."
  exec 3>&- 2>/dev/null || true
  exit 3
fi

if [ "$CONFIRM" != "si" ]; then
  echo ""
  echo "Il ripristino sovrascrive database, progetti, export e originali con il contenuto del backup."
  echo "Prima di procedere viene creato automaticamente un backup di sicurezza dello stato attuale,"
  echo "quindi l'operazione resta annullabile."
  printf "Continuare? [s/N] "
  read -r answer
  case "$answer" in
    s|S|si|SI|sì) ;;
    *) echo "Annullato."; exit 0 ;;
  esac
fi

TARGET_ENV="$TARGET" "$PY" - <<'PYCODE'
import os
import sys
from pathlib import Path

from kora_studio.backup.backup_manager import BackupManager
from kora_studio.config import BACKUPS_DIR
from kora_studio.db.database import Database

raw = os.environ.get("TARGET_ENV", "")
if raw == "__LATEST__":
    candidates = sorted(BACKUPS_DIR.glob("kora_studio_backup_*.zip"))
    if not candidates:
        print("Nessun backup da ripristinare.")
        sys.exit(1)
    target = candidates[-1]
else:
    target = Path(raw).expanduser().resolve()

print(f"Ripristino da: {target}")
manager = BackupManager(Database())
result = manager.restore_backup(target, safety_backup=True)
print(f"File ripristinati: {result.get('restored_files')}")
print(f"Destinazione: {result.get('restored_to')}")
if result.get("safety_backup"):
    print(f"Backup di sicurezza (per annullare): {result['safety_backup']}")
else:
    print("Attenzione: backup di sicurezza non creato. Verifica lo spazio su disco.")
print("Riapri KORA con «Avvia KORA.command» (o ./start.sh) e controlla i progetti.")
PYCODE
