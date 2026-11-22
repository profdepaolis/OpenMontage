#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6 — avvio del server locale.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 1

HOST="${KORA_HOST:-127.0.0.1}"
PORT="${KORA_PORT:-8020}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Ambiente non pronto: eseguo setup.sh..."
  ./setup.sh || exit 1
fi

if [ ! -f "kora_studio/frontend/out/index.html" ]; then
  echo "Interfaccia web non compilata: eseguo setup.sh..."
  ./setup.sh || exit 1
fi

export PATH="$ROOT_DIR/.venv/bin:$PATH"
export KORA_PORT="$PORT"

echo "KORA STUDIO → http://$HOST:$PORT"
echo "Per fermare: Ctrl+C in questa finestra."
echo

exec .venv/bin/python -m kora_studio serve --host "$HOST" --port "$PORT"
