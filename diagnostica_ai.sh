#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6.1 — diagnostica del motore creativo (macOS/Linux)
#
# Uso:  ./diagnostica_ai.sh            (server su 127.0.0.1:8020)
#       KORA_PORT=8023 ./diagnostica_ai.sh
#
# Non modifica nulla: legge lo stato, esegue un test rapido e un test creativo
# reale sul modello Everyday configurato e misura la latenza.
set -u

HOST="${KORA_HOST_HTTP:-127.0.0.1}"
PORT="${KORA_PORT:-8020}"
BASE="http://${HOST}:${PORT}"
OK=0
KO=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  OK   %s\n' "$*"; OK=$((OK+1)); }
ko()   { printf '  KO   %s\n' "$*"; KO=$((KO+1)); }
warn() { printf '  !!!  %s\n' "$*"; }

command -v curl >/dev/null 2>&1 || { say "curl non disponibile: impossibile procedere."; exit 2; }

say ""
say "KORA STUDIO LIGHT — diagnostica motore creativo"
say "Server: ${BASE}"
say "──────────────────────────────────────────────────────────────"

if ! curl -fsS --max-time 5 "${BASE}/api/health" >/dev/null 2>&1; then
  ko "il server non risponde su ${BASE}: avvia KORA (./start.sh o «Avvia KORA.command»)"
  say ""
  say "Risultato: ${OK} OK · ${KO} KO"
  exit 1
fi
ok "server raggiungibile"

STATUS_JSON="$(curl -fsS --max-time 20 "${BASE}/api/ai-models/status" 2>/dev/null || true)"
if [ -z "${STATUS_JSON}" ]; then
  ko "endpoint /api/ai-models/status non disponibile (versione precedente alla 3.6.1?)"
  say ""
  say "Risultato: ${OK} OK · ${KO} KO"
  exit 1
fi

read_field() {  # read_field <json> <python expression on d>
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
data = json.loads(sys.argv[1])
d = data
expr = sys.argv[2]
try:
    value = eval(expr, {"d": d})
except Exception:
    value = ""
if isinstance(value, bool):
    print("sì" if value else "no")
elif value is None:
    print("—")
else:
    print(value)
PY
}

PROVIDER="$(read_field "${STATUS_JSON}" "d.get('provider','')")"
MODEL="$(read_field "${STATUS_JSON}" "d.get('model','')")"
MODE="$(read_field "${STATUS_JSON}" "d.get('mode','')")"
POLICY="$(read_field "${STATUS_JSON}" "d.get('policy','')")"
INSTALLED="$(read_field "${STATUS_JSON}" "d.get('installed',False)")"
LAST_INF="$(read_field "${STATUS_JSON}" "(d.get('last_inference') or {}).get('ok')")"
LAST_INF_AT="$(read_field "${STATUS_JSON}" "(d.get('last_inference') or {}).get('at','')")"
MIGRATION="$(read_field "${STATUS_JSON}" "(d.get('migration') or {}).get('detail','')")"

say ""
say "Configurazione attiva (fonte unica: Model Manager)"
say "  provider ....... ${PROVIDER:-—}"
say "  modello ........ ${MODEL:-—}"
say "  modalità ....... ${MODE:-—}"
say "  politica ....... ${POLICY:-—}"
say "  CLI installata . ${INSTALLED:-—}"
say "  ultima inferenza ${LAST_INF:-—} ${LAST_INF_AT:+(${LAST_INF_AT})}"
[ -n "${MIGRATION}" ] && say "  migrazione ..... ${MIGRATION}"

[ "${INSTALLED}" = "sì" ] && ok "CLI opencode installata" || ko "CLI opencode non trovata nel PATH"
case "${MODEL}" in
  opencode/hy4-flash-free|opencode/mimo-v2.6-flash-free|opencode/nemotron-3.5-lightning-free)
    ok "modello configurato fra quelli verificati (${MODEL})" ;;
  *deepseek*)
    ko "modello dismesso ancora configurato (${MODEL}): esegui ./diagnostica_ai.sh --fix" ;;
  *) warn "modello non presente fra i tre verificati (${MODEL})" ;;
esac

if [ "${1:-}" = "--fix" ]; then
  say ""
  say "Migrazione della configurazione legacy…"
  curl -fsS --max-time 30 -X POST "${BASE}/api/ai-models/migrate-legacy" >/dev/null 2>&1 \
    && ok "migrazione eseguita (rileggi lo stato qui sopra)" \
    || ko "migrazione non riuscita"
fi

say ""
say "Test rapido reale (atteso < 10 s)"
QUICK="$(curl -fsS --max-time 60 -X POST "${BASE}/api/ai-models/test" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${MODEL}\",\"kind\":\"quick\"}" 2>/dev/null || true)"
QUICK_OK="$(read_field "${QUICK:-{\}}" "d.get('ok')")"
QUICK_MS="$(read_field "${QUICK:-{\}}" "d.get('latency_ms')")"
QUICK_ERR="$(read_field "${QUICK:-{\}}" "d.get('error')")"
if [ "${QUICK_OK}" = "sì" ]; then
  ok "risposta valida in ${QUICK_MS} ms"
else
  ko "test rapido non superato${QUICK_ERR:+: ${QUICK_ERR}}"
fi

say ""
say "Test creativo reale (4 temi, stessa richiesta di «Suggerisci un tema»)"
CREATIVE="$(curl -fsS --max-time 200 -X POST "${BASE}/api/ai-models/test" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${MODEL}\",\"kind\":\"creative\"}" 2>/dev/null || true)"
CREATIVE_OK="$(read_field "${CREATIVE:-{\}}" "d.get('ok')")"
CREATIVE_MS="$(read_field "${CREATIVE:-{\}}" "d.get('latency_ms')")"
CREATIVE_THEMES="$(read_field "${CREATIVE:-{\}}" "d.get('themes')")"
CREATIVE_ERR="$(read_field "${CREATIVE:-{\}}" "d.get('error')")"
if [ "${CREATIVE_OK}" = "sì" ]; then
  ok "4 temi validi in ${CREATIVE_MS} ms ($(python3 -c "print(round(${CREATIVE_MS:-0}/1000,1))" 2>/dev/null || echo "?") s)"
else
  ko "test creativo non superato (temi: ${CREATIVE_THEMES:-0})${CREATIVE_ERR:+: ${CREATIVE_ERR}}"
fi

say ""
say "Ultime chiamate al motore (Monitor AI)"
curl -fsS --max-time 15 "${BASE}/api/ai-engine/activity?limit=5" 2>/dev/null | python3 - <<'PY' 2>/dev/null || warn "monitor non leggibile"
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
rows = rows if isinstance(rows, list) else rows.get("calls", [])
for row in rows[:5]:
    print(f"  {row.get('started_at','')[:19]:<19} {row.get('agent',''):<18} "
          f"{row.get('provider','')}/{row.get('model',''):<32} {row.get('outcome','')}")
PY

say "──────────────────────────────────────────────────────────────"
say "Risultato: ${OK} OK · ${KO} KO"
if [ "${KO}" -eq 0 ]; then
  say "Motore creativo operativo: «Suggerisci un tema» deve produrre 4 proposte AI."
  exit 0
fi
say "Se il KO riguarda installazione/autenticazione:"
say "  opencode --version            # il CLI risponde?"
say "  opencode auth list            # l'account è collegato?"
say "Poi riprova: ./diagnostica_ai.sh"
exit 1
