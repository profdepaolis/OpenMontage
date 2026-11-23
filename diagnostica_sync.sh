#!/usr/bin/env bash
# KORA Studio Light 3.6.6 — diagnostica della sincronizzazione
# Uso:
#   ./diagnostica_sync.sh                 # tutti i progetti
#   ./diagnostica_sync.sh prj_xxxxxxxx    # un solo progetto
#   KORA_PORT=8021 ./diagnostica_sync.sh
set -uo pipefail

PORTA="${KORA_PORT:-8020}"
BASE="http://127.0.0.1:${PORTA}"

ok()   { printf '  OK   %s\n' "$1"; }
ko()   { printf '  KO   %s\n' "$1"; }
info() { printf '  ..   %s\n' "$1"; }
titolo(){ printf '\n%s\n%s\n' "$1" "$(printf '%.0s-' $(seq 1 ${#1}))"; }

PY=""
for cand in .venv/bin/python python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import json' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "Serve Python 3 per leggere le risposte JSON."; exit 1
fi

titolo "Server"
if curl -fsS "${BASE}/api/health" >/dev/null 2>&1; then
  ok "KORA risponde su ${BASE}"
else
  ko "KORA non risponde su ${BASE}: avvia con ./start.sh"
  exit 1
fi

titolo "Motore di trascrizione (ASR)"
curl -fsS "${BASE}/api/asr/status" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print("  " + ("OK  " if d.get("available") else "..  ") + d.get("message",""))
if not d.get("available"):
    print("       nessun testo verrà inventato: incolla il copione o correggi a mano")
'

PROGETTI="${1:-}"
if [ -z "$PROGETTI" ]; then
  PROGETTI="$(curl -fsS "${BASE}/api/projects" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
items=d if isinstance(d,list) else d.get("projects") or d.get("items") or []
print(" ".join(p.get("id","") for p in items if p.get("id")))')"
fi

if [ -z "$PROGETTI" ]; then
  titolo "Progetti"; info "nessun progetto presente"; exit 0
fi

for PID in $PROGETTI; do
  titolo "Progetto ${PID}"
  curl -fsS "${BASE}/api/projects/${PID}/sync/verify" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
issues=d.get("issues") or []
if d.get("ok"): print("  OK   nessun conflitto tra voce, scene e sottotitoli")
else: print("  KO   conflitti da risolvere")
print("  ..   allineamento: %s · parole: %s · cue: %s" % (d.get("alignment"), d.get("words"), d.get("cues")))
print("  ..   verificata sulle parole pronunciate: %s" % ("sì" if d.get("verified") else "no (tempi stimati o corretti a mano)"))
audio=d.get("audio") or {}
if audio:
    print("  ..   voce %.2fs → %.2fs · offset dichiarato %.2fs · durata sorgente %.2fs" % (
        float(audio.get("start_offset_sec") or 0.0), float(audio.get("end_sec") or 0.0),
        float(audio.get("start_offset_sec") or 0.0), float(audio.get("duration_sec") or 0.0)))
for i in issues:
    tag="KO  " if i.get("severity")=="error" else "..  "
    print("  %s %s" % (tag, i.get("message")))
'
done

titolo "Suggerimenti"
echo "  Ricalcola:   curl -s -X POST ${BASE}/api/projects/ID/timeline/rebuild -H 'Content-Type: application/json' -d '{}'"
echo "  Verifica:    curl -s -X POST ${BASE}/api/projects/ID/sync/verify"
echo "  Artefatti:   kora_data/exports/ID/ID_sottotitoli.srt · .vtt · .ass · _cues.json · _timeline.json"
