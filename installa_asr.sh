#!/usr/bin/env bash
# KORA Studio Light 3.6.8 — installazione facoltativa del motore di trascrizione (ASR)
#
#   ./installa_asr.sh                 # chiede conferma, livello consigliato
#   ./installa_asr.sh --livello rapido
#   ./installa_asr.sh --si            # nessuna domanda
#   ./installa_asr.sh --verifica      # solo controllo, nessuna installazione
#
# Installa faster-whisper NELL'AMBIENTE DI KORA (.venv della cartella), mai in un
# Python di sistema diverso. Nessun servizio cloud, nessun costo.
set -uo pipefail

CARTELLA="$(cd "$(dirname "$0")" && pwd)"
LIVELLO="bilanciato"
SI=0
VERIFICA=0

while [ $# -gt 0 ]; do
  case "$1" in
    --livello) LIVELLO="${2:-bilanciato}"; shift 2 ;;
    --cartella) CARTELLA="${2:-$CARTELLA}"; shift 2 ;;
    --si|-y) SI=1; shift ;;
    --verifica) VERIFICA=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Opzione sconosciuta: $1"; exit 2 ;;
  esac
done

ok()   { printf '  OK   %s\n' "$1"; }
ko()   { printf '  KO   %s\n' "$1"; }
info() { printf '  ..   %s\n' "$1"; }
warn() { printf '  !!   %s\n' "$1"; }
titolo(){ printf '\n%s\n%s\n' "$1" "$(printf '%.0s-' $(seq 1 ${#1}))"; }

case "$LIVELLO" in
  rapido)      MODELLO="tiny";  PESO_MB=80 ;;
  bilanciato)  MODELLO="base";  PESO_MB=150 ;;
  accurato)    MODELLO="small"; PESO_MB=500 ;;
  *) echo "Livello non valido: $LIVELLO (rapido|bilanciato|accurato)"; exit 2 ;;
esac

PY="$CARTELLA/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Ambiente KORA non trovato in $CARTELLA/.venv"
  echo "Avvia prima «Installa KORA.command» nella cartella dell'applicazione."
  exit 1
fi

titolo "1/5 Compatibilità della macchina"
ARCH="$(uname -m)"
OSVER="$(sw_vers -productVersion 2>/dev/null || echo "n/d")"
if [ "$ARCH" = "arm64" ]; then
  ok "Apple Silicon ($ARCH) · macOS $OSVER"
elif [ "$ARCH" = "x86_64" ]; then
  ok "Intel ($ARCH) · macOS $OSVER — trascrizione su CPU, modelli piccoli consigliati"
else
  warn "architettura non riconosciuta: $ARCH (proseguo, potrebbe essere lenta)"
fi

PYVER="$("$PY" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo "?")"
ok "Python di KORA: $PYVER ($PY)"
if "$PY" -c 'import sys;raise SystemExit(0 if (3,9) <= sys.version_info[:2] else 1)'; then
  ok "versione Python compatibile (>= 3.9)"
else
  ko "serve Python >= 3.9"; exit 1
fi

MEM_GB="$("$PY" - <<'PY'
import os
try:
    import subprocess
    out = subprocess.run(["sysctl","-n","hw.memsize"],capture_output=True,text=True).stdout.strip()
    print(round(int(out)/1e9,1) if out else "n/d")
except Exception:
    print("n/d")
PY
)"
if [ "$MEM_GB" = "n/d" ]; then
  warn "memoria non rilevata"
elif "$PY" -c "raise SystemExit(0 if float('$MEM_GB') >= 4 else 1)"; then
  ok "memoria disponibile: ${MEM_GB} GB (sufficiente per il modello «$MODELLO»)"
else
  warn "memoria ${MEM_GB} GB: usa il livello «rapido» per stare tranquillo"
fi

LIBERO_MB="$(df -Pm "$CARTELLA" | awk 'NR==2{print $4}')"
RICHIESTI_MB=$(( PESO_MB + 400 ))
if [ "${LIBERO_MB:-0}" -ge "$RICHIESTI_MB" ]; then
  ok "spazio libero: ${LIBERO_MB} MB (servono ~${RICHIESTI_MB} MB)"
else
  ko "spazio insufficiente: ${LIBERO_MB} MB liberi, servono ~${RICHIESTI_MB} MB"
  exit 1
fi

if [ "$VERIFICA" -eq 1 ]; then
  titolo "Solo verifica richiesta"
  info "nessuna installazione eseguita"
  exit 0
fi

titolo "2/5 Riepilogo"
echo "  motore ......  faster-whisper (locale, CPU, int8)"
echo "  livello .....  $LIVELLO → modello «$MODELLO» (~${PESO_MB} MB, scaricato una volta)"
echo "  ambiente ....  $CARTELLA/.venv"
echo "  costo .......  nessuno (nessun servizio cloud)"
if [ "$SI" -eq 0 ]; then
  printf "\n  Procedo? [s/N] "
  read -r risposta
  case "$risposta" in s|S|si|SI|y|Y) ;; *) echo "  annullato"; exit 0 ;; esac
fi

titolo "3/5 Installazione del pacchetto"
if "$PY" -c 'import faster_whisper' 2>/dev/null; then
  ok "faster-whisper già presente"
else
  info "pip install faster-whisper (può richiedere qualche minuto)…"
  if "$PY" -m pip install --upgrade pip >/dev/null 2>&1; then ok "pip aggiornato"; fi
  if "$PY" -m pip install faster-whisper; then
    ok "faster-whisper installato"
  else
    ko "installazione non riuscita (rete o compilazione)"
    echo "  Alternativa: $PY -m pip install openai-whisper"
    exit 1
  fi
fi

titolo "4/5 Modello «$MODELLO»"
info "primo uso: download del modello (una sola volta, resta nella cache dell'utente)"
"$PY" - "$MODELLO" <<'PY'
import sys
from faster_whisper import WhisperModel
size = sys.argv[1]
model = WhisperModel(size, device="cpu", compute_type="int8")
print(f"  OK   modello «{size}» caricato")
PY
if [ $? -ne 0 ]; then
  ko "modello non scaricabile in questo momento"
  exit 1
fi

titolo "5/5 Prova reale di trascrizione"
FILE_AUDIO=""
for cand in "$CARTELLA"/kora_data/voiceovers/*; do
  [ -f "$cand" ] && FILE_AUDIO="$cand" && break
done
if [ -z "$FILE_AUDIO" ]; then
  for cand in "$CARTELLA"/assets/kora/voices/*; do
    [ -f "$cand" ] && FILE_AUDIO="$cand" && break
  done
fi
if [ -z "$FILE_AUDIO" ]; then
  warn "nessun file audio trovato: carica un MP3 in KORA e rilancia con --verifica dopo la trascrizione"
  exit 0
fi
info "provo su: $(basename "$FILE_AUDIO")"
"$PY" - "$FILE_AUDIO" "$MODELLO" <<'PY'
import sys, json, pathlib
from faster_whisper import WhisperModel
path, size = sys.argv[1], sys.argv[2]
model = WhisperModel(size, device="cpu", compute_type="int8")
segments, info = model.transcribe(path, language="it", beam_size=5, vad_filter=True, word_timestamps=True)
words = []
text = []
for seg in segments:
    text.append(seg.text.strip())
    for w in (seg.words or []):
        words.append({"word": w.word.strip(), "start_sec": round(w.start, 3), "end_sec": round(w.end, 3)})
report = {"file": str(path), "model": size, "language": getattr(info, "language", "it"),
          "n_words": len(words), "timestamp_disponibili": bool(words),
          "primi": words[:8], "testo": " ".join(text)[:400]}
out = pathlib.Path("/tmp/kora_asr_report.json")
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("  OK   parole con timestamp:", len(words))
for w in words[:5]:
    print(f"       {w['start_sec']:.2f}s → {w['end_sec']:.2f}s  {w['word']}")
print("  ..   report:", out)
if not words:
    print("  !!   nessun timestamp: il modello non è adatto a questo audio")
    sys.exit(1)
PY

titolo "Fatto"
echo "  Riavvia KORA (./start.sh) e in Storyboard scegli «Trascrivi automaticamente»."
echo "  Verifica:  ./diagnostica_sync.sh"
