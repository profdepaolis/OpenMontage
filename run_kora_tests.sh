#!/usr/bin/env bash
# KORA STUDIO 2.8 — single reproducible test command
# Usage: ./run_kora_tests.sh            (full suite)
#        ./run_kora_tests.sh -k vision  (subset, any pytest args are forwarded)
set -euo pipefail
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi
export PYTHONPATH="${PWD}${PYTHONPATH:+:$PYTHONPATH}"
# ffmpeg/ffprobe are expected on PATH or inside .venv/bin
if [ -d ".venv/bin" ]; then
  export PATH="${PWD}/.venv/bin:${PATH}"
fi
exec "$PY" -m pytest tests/kora_studio -q "$@"
