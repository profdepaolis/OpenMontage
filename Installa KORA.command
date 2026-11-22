#!/bin/bash
# KORA STUDIO LIGHT 3.6 — installazione e avvio con doppio clic.
# Fa tutto: controlla Python, prepara l'ambiente, verifica e avvia KORA.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
PORT="${KORA_PORT:-8020}"

echo "======================================================"
echo "  KORA STUDIO LIGHT 3.6 · The Art of Being"
echo "======================================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 non è installato."
  echo "Scaricalo da https://www.python.org/downloads/ (versione 3.11 o superiore),"
  echo "poi richiudi e riapri questo file."
  echo
  read -r -p "Premi Invio per chiudere..." _
  exit 1
fi

echo "[1/3] Preparazione dell'ambiente (la prima volta può richiedere 2-3 minuti)..."
if ! ./setup.sh; then
  echo
  echo "La preparazione ha avuto un problema (vedi sopra)."
  echo "Puoi riprovare a lanciare questo file, oppure eseguire ./verifica.sh per i dettagli."
  echo
  read -r -p "Premi Invio per chiudere..." _
  exit 1
fi

echo
echo "[2/3] Verifica dell'installazione..."
./verifica.sh || echo "(avvisi riportati sopra: KORA può comunque partire)"

echo
echo "[3/3] Avvio di KORA STUDIO su http://127.0.0.1:${PORT}"
echo "      Lascia aperta questa finestra: è il motore di KORA."
echo "      Per fermare tutto: Ctrl+C qui dentro."
echo
( sleep 6
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1
  fi
) &

export KORA_PORT="${PORT}"
exec ./start.sh
