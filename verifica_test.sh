#!/usr/bin/env bash
# KORA Studio Light 3.6.8 — esegue la suite di test dentro l'ambiente di KORA
#
#   ./verifica_test.sh            # suite completa (15-20 min)
#   ./verifica_test.sh -k timeline   # solo i test che contengono "timeline"
#   ./verifica_test.sh --veloce   # SOLO i test di sincronizzazione (veloce)
set -uo pipefail
CARTELLA="$(cd "$(dirname "$0")" && pwd)"
cd "$CARTELLA" || exit 1
PY="$CARTELLA/.venv/bin/python"
[ -x "$PY" ] || { echo "Ambiente KORA non trovato: lancia prima «Installa KORA.command»"; exit 1; }

if ! "$PY" -c 'import pytest' 2>/dev/null; then
  echo "  ..   installo pytest e httpx (solo per i test)…"
  "$PY" -m pip install -q pytest httpx || { echo "installazione non riuscita"; exit 1; }
fi

# 3.6.8 — "--veloce" esegue SOLO i test di sincronizzazione, non la suite intera.
TARGETS=("tests/kora_studio")
if [ "${1:-}" = "--veloce" ]; then
  shift
  TARGETS=(
    "tests/kora_studio/test_v366_audio_timeline.py"
    "tests/kora_studio/test_v367_speech_sync.py"
    "tests/kora_studio/test_v368_sync_regression.py"
  )
  echo "Modalità veloce: solo i test di sincronizzazione (3.6.6 / 3.6.7 / 3.6.8)."
fi
if [ "$#" -gt 0 ]; then
  # argomenti extra (es. -k parola) passano a pytest; percorsi espliciti sostituiscono i target
  case "${1:-}" in
    tests/*|*/test_*.py) TARGETS=() ;;
  esac
fi
LOG="/tmp/kora_test_$(date +%Y%m%d-%H%M%S).log"
echo "Suite KORA · log: $LOG"
"$PY" -m pytest ${TARGETS[@]+"${TARGETS[@]}"} "$@" -q 2>&1 | tee "$LOG"
ESITO=${PIPESTATUS[0]}
echo
if [ "$ESITO" -eq 0 ]; then echo "  OK   test superati"; else echo "  KO   test falliti (vedi $LOG)"; fi
exit "$ESITO"
