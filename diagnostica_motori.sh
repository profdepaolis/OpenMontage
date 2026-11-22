#!/usr/bin/env bash
# KORA STUDIO 3.6.2 — diagnosi dei motori audiovisivi (solo locale, nessuna installazione)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
  else
    echo "Python non trovato: installa Python 3.11+ oppure crea il venv con ./setup.sh" >&2
    exit 2
  fi
fi

TEST_FLAG=""
if [ "${1:-}" = "--test" ]; then
  TEST_FLAG="--test"
fi

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PY}" -m kora_studio.media.engines ${TEST_FLAG}
