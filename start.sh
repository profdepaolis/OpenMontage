#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${KORA_HOST:-0.0.0.0}"
PORT="${KORA_PORT:-8000}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Ambiente .venv non trovato. Eseguo ./setup.sh..."
  ./setup.sh
fi

export PATH="$ROOT_DIR/.venv/bin:$PATH"

echo "Avvio KORA STUDIO su http://$HOST:$PORT ..."
exec .venv/bin/python -m kora_studio serve --host "$HOST" --port "$PORT"
