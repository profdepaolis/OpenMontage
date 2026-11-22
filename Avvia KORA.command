#!/bin/bash
# KORA STUDIO LIGHT 3.6 — avvio rapido (installazione già fatta).
set -uo pipefail
cd "$(dirname "$0")" || exit 1
PORT="${KORA_PORT:-8020}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Ambiente non ancora pronto: eseguo l'installazione..."
  exec "./Installa KORA.command"
fi

echo "KORA STUDIO → http://127.0.0.1:${PORT}   (Ctrl+C per fermare)"
( sleep 5
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1
  fi
) &
export KORA_PORT="${PORT}"
exec ./start.sh
