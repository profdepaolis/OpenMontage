#!/usr/bin/env bash
# KORA STUDIO LIGHT 3.6.3 — aggiornamento completo da terminale.
#
# Fa tutto, in ordine, senza doppio clic e senza cancellare nulla dei tuoi dati:
#   1. trova l'installazione esistente (o ne crea una nuova)
#   2. backup preventivo: database, progetti e copia del codice attuale
#   3. ferma il server se è in esecuzione
#   4. scarica (o usa) il pacchetto 3.6.3 e lo verifica
#   5. aggiorna SOLO l'applicazione: kora_data/ (foto, progetti, DB, voci,
#      loghi, Reel) non viene mai toccata
#   6. setup + verifica + avvio + diagnostica AI
#   7. se qualcosa fallisce, rimette il codice precedente (i dati non si toccano)
#
# Uso:
#   ./aggiorna_kora.sh                       # scarica 3.6.3 e aggiorna
#   ./aggiorna_kora.sh --da-zip ~/Downloads/KORA_Studio_Light_3.6.3_installer.zip
#   ./aggiorna_kora.sh --cartella ~/Documenti/"Kora Studio Light"
#   ./aggiorna_kora.sh --solo-verifica       # nessuna modifica: verifica + diagnostica
#   ./aggiorna_kora.sh --no-avvio            # aggiorna e verifica, non avvia il server
#   KORA_PORT=8021 ./aggiorna_kora.sh        # se la porta 8020 è occupata
#   KORA_ZIP_URL=https://…/file.zip ./aggiorna_kora.sh   # sorgente alternativa
#
set -uo pipefail

VERSIONE="3.6.3"
ZIP_ID="1Wl8iS5uRMUzobRz1UC3OAimY-eZ70tSw"
ZIP_NOME="KORA_Studio_Light_${VERSIONE}_installer.zip"
PORTA="${KORA_PORT:-8020}"
LAVORO="$(mktemp -d "${TMPDIR:-/tmp}/kora_agg.XXXXXX")"
ZIP_SCARICATO=""
CARTELLA=""
DB_BACKUP=""
SOLO_VERIFICA=0
AVVIA=1
BACKUP_CODICE=""
TAPPA=""

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  OK   %s\n' "$*"; }
warn()  { printf '  ..   %s\n' "$*"; }
ko()    { printf '  KO   %s\n' "$*"; }
titolo(){ printf '\n[%s] %s\n' "$1" "$2"; }

pulizia() { rm -rf "$LAVORO"; }
trap pulizia EXIT

# ------------------------------------------------------------------ argomenti
MODELLO_ARGOMENTI=0
while [ $# -gt 0 ]; do
  case "$1" in
    --da-zip)        ZIP_SCARICATO="${2:-}"; shift 2 ;;
    --cartella)      CARTELLA="${2:-}"; shift 2 ;;
    --porta)         PORTA="${2:-$PORTA}"; shift 2 ;;
    --solo-verifica) SOLO_VERIFICA=1; shift ;;
    --no-avvio)      AVVIA=0; shift ;;
    -h|--help)       sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Argomento non riconosciuto: $1 (usa --help)" >&2; exit 2 ;;
  esac
done
[ -n "$ZIP_SCARICATO" ] && MODELLO_ARGOMENTI=1

echo "======================================================"
echo "  KORA STUDIO LIGHT ${VERSIONE} · aggiornamento da terminale"
echo "======================================================"

# ------------------------------------------------------- 1. installazione
trova_installazione() {
  local candidate
  for candidate in \
    "$CARTELLA" \
    "$HOME/Documenti/Kora Studio Light" \
    "$HOME/Documents/Kora Studio Light" \
    "$HOME/Desktop/Kora Studio Light" \
    "$HOME/Kora Studio Light" \
    "/Applications/Kora Studio Light" \
    "$(pwd)"
  do
    [ -n "$candidate" ] || continue
    if [ -f "$candidate/kora_studio/api/app.py" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  # ultima spiaggia: prima cartella trovata con l'app dentro
  find "$HOME" -maxdepth 4 -type f -path "*/kora_studio/api/app.py" 2>/dev/null | head -1 | sed 's#/kora_studio/api/app.py$##'
}

titolo "1/7" "Installazione"
if [ -z "$CARTELLA" ]; then
  CARTELLA="$(trova_installazione)"
fi
if [ -n "$CARTELLA" ] && [ -f "$CARTELLA/kora_studio/api/app.py" ]; then
  CARTELLA="$(cd "$CARTELLA" && pwd)"
  ok "installazione trovata: $CARTELLA"
  VECCHIA_VERSIONE="$(sed -n 's/.*version="\([0-9.]*\)".*/\1/p' "$CARTELLA/kora_studio/api/app.py" | head -1)"
  info "versione attuale: ${VECCHIA_VERSIONE:-sconosciuta}"
else
  CARTELLA="$HOME/Documenti/Kora Studio Light"
  warn "nessuna installazione trovata: la creo in $CARTELLA"
  mkdir -p "$CARTELLA" || { ko "impossibile creare $CARTELLA"; exit 1; }
fi
DATI="$CARTELLA/kora_data"
mkdir -p "$DATI"

# --------------------------------------------------------------- 2. backup
titolo "2/7" "Backup preventivo (nessun dato verrà cancellato)"
MARCATEMPO="$(date +%Y%m%d-%H%M%S)"
if [ -d "$DATI" ]; then
  mkdir -p "$DATI/backups"
  if [ -f "$DATI/kora_studio.db" ]; then
    if cp -p "$DATI/kora_studio.db" "$DATI/backups/pre_${VERSIONE}_${MARCATEMPO}.db"; then
      DB_BACKUP="$DATI/backups/pre_${VERSIONE}_${MARCATEMPO}.db"
      ok "database copiato in kora_data/backups/pre_${VERSIONE}_${MARCATEMPO}.db"
    else
      warn "copia del database non riuscita (continuo: il DB non viene toccato)"
    fi
  else
    warn "database non ancora presente (prima installazione)"
  fi
  CONTEGGIO_FOTO="$(find "$DATI/vault" -type f 2>/dev/null | wc -l | tr -d ' ')"
  CONTEGGIO_PROGETTI="$(find "$DATI/projects" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
  info "Media Vault: $CONTEGGIO_FOTO file · Progetti: $CONTEGGIO_PROGETTI"
fi
if [ "$SOLO_VERIFICA" -eq 0 ] && [ -f "$CARTELLA/kora_studio/api/app.py" ]; then
  BACKUP_CODICE="$HOME/KORA_backup_app_${MARCATEMPO}.tar.gz"
  if tar -czf "$BACKUP_CODICE" -C "$CARTELLA" \
       --exclude './kora_data' --exclude './.venv' --exclude './demo' \
       --exclude './__pycache__' . 2>/dev/null; then
    ok "codice attuale salvato in $BACKUP_CODICE"
  else
    BACKUP_CODICE=""
    warn "copia del codice non riuscita: procedo comunque (i dati restano intatti)"
  fi
fi

# ---------------------------------------------- solo verifica: salta al 6/7
if [ "$SOLO_VERIFICA" -eq 1 ]; then
  info "modalità --solo-verifica: nessuna modifica ai file"
  TAPPA="verifica"
fi

# ------------------------------------------------- 3. ferma il server attivo
porta_in_uso() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORTA" 2>/dev/null | head -1
    return 0
  fi
  # macOS senza lsof: cerco il processo che serve questa porta
  pgrep -f "kora_studio.*(--port|${PORTA})" 2>/dev/null | while read -r p; do
    if ps -o command= -p "$p" 2>/dev/null | grep -q -- "--port ${PORTA}\b"; then
      echo "$p"; break
    fi
  done
}

ferma_server() {
  local pid
  pid="$(porta_in_uso)"
  if [ -z "$pid" ]; then
    warn "nessun server in ascolto sulla porta $PORTA (già fermo)"
    return 0
  fi
  info "fermo il server (pid $pid, porta $PORTA)…"
  kill -TERM "$pid" 2>/dev/null
  local atteso=0
  while [ "$atteso" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
    sleep 0.5; atteso=$((atteso + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null
    warn "server terminato con SIGKILL"
  else
    ok "server fermato"
  fi
}

# --------------------------------------------------- 4. pacchetto 3.6.3
scarica_pacchetto() {
  if [ -n "$ZIP_SCARICATO" ]; then
    [ -f "$ZIP_SCARICATO" ] || { ko "file non trovato: $ZIP_SCARICATO"; exit 1; }
    ok "uso il pacchetto indicato: $ZIP_SCARICATO"
    return 0
  fi
  local destinazione="$LAVORO/$ZIP_NOME"
  local url="${KORA_ZIP_URL:-https://drive.google.com/uc?export=download&id=${ZIP_ID}}"
  info "scarico $ZIP_NOME…"
  if ! curl -fsSL --retry 3 --retry-delay 2 -o "$destinazione" "$url" 2>"$LAVORO/curl.err"; then
    cp "$LAVORO/curl.err" "$LAVORO/ultimo.errore" 2>/dev/null
    ko "download non riuscito"; sed 's/^/       /' "$LAVORO/curl.err" | tail -3
    echo
    echo "  Rimedio: scarica il file dal browser e rilancia:"
    echo "    ./aggiorna_kora.sh --da-zip ~/Downloads/${ZIP_NOME}"
    exit 1
  fi
  # Drive a volte restituisce una pagina HTML di conferma invece dello ZIP
  if ! unzip -tqq "$destinazione" >/dev/null 2>&1; then
    warn "il download non è uno ZIP valido: provo il link di conferma di Drive"
    local url2="https://drive.usercontent.google.com/download?id=${ZIP_ID}&export=download&confirm=t"
    if ! curl -fsSL --retry 2 -o "$destinazione" "$url2" 2>/dev/null || ! unzip -tqq "$destinazione" >/dev/null 2>&1; then
      ko "il file scaricato non è un archivio valido"
      echo "  Rimedio: scarica dal browser e usa --da-zip (vedi sopra)."
      exit 1
    fi
  fi
  ok "pacchetto scaricato ($(wc -c <"$destinazione" | tr -d ' ') byte)"
  ZIP_SCARICATO="$destinazione"
}

estrai_pacchetto() {
  local estratto="$LAVORO/estratto"
  mkdir -p "$estratto"
  unzip -q "$ZIP_SCARICATO" -d "$estratto" || { ko "estrazione non riuscita"; exit 1; }
  local radice
  radice="$(find "$estratto" -maxdepth 2 -type f -name "setup.sh" | head -1 | xargs -I{} dirname {})"
  [ -n "$radice" ] && [ -f "$radice/kora_studio/api/app.py" ] || { ko "pacchetto incompleto (setup.sh/app non trovati)"; exit 1; }
  # solo il percorso va su stdout: i messaggi restano su stderr
  ok "pacchetto valido: $(basename "$radice")" >&2
  printf '%s' "$radice"
}

# ------------------------------------------------------ 5. applica update
copia_albero() { # sorgente destinazione · sostituisce il contenuto (solo codice)
  local sorgente="$1" destinazione="$2"
  [ -d "$sorgente" ] || return 0
  rm -rf "$destinazione"
  mkdir -p "$destinazione"
  ( cd "$sorgente" && tar -cf - . ) | ( cd "$destinazione" && tar -xf - )
}

integra_albero() { # come sopra ma senza cancellare file esistenti (demo)
  local sorgente="$1" destinazione="$2"
  [ -d "$sorgente" ] || return 0
  mkdir -p "$destinazione"
  ( cd "$sorgente" && tar -cf - . ) | ( cd "$destinazione" && tar -xf - )
}

aggiorna_file() {
  local sorgente="$1"
  # applicazione, asset, documenti: sostituiti per intero (nessun dato utente)
  copia_albero "$sorgente/kora_studio" "$CARTELLA/kora_studio" || return 1
  [ -d "$sorgente/assets" ] && { copia_albero "$sorgente/assets" "$CARTELLA/assets" || return 1; }
  [ -d "$sorgente/docs" ] && { copia_albero "$sorgente/docs" "$CARTELLA/docs" || return 1; }
  # demo: aggiornate senza cancellare eventuali file tuoi
  [ -d "$sorgente/demo" ] && { integra_albero "$sorgente/demo" "$CARTELLA/demo" || return 1; }
  # script e documenti di primo livello (mai kora_data, .venv, chiavi)
  local f
  for f in setup.sh verifica.sh start.sh ripristina.sh diagnostica_ai.sh aggiorna_kora.sh \
           "Installa KORA.command" "Avvia KORA.command" \
           LEGGIMI.txt INSTALLAZIONE.md KORA_3.6_REPORT.md requirements.txt; do
    [ -f "$sorgente/$f" ] && cp -p "$sorgente/$f" "$CARTELLA/$f"
  done
  # lo strumento di aggiornamento resta disponibile nella cartella
  if [ -f "$0" ] && [ "$(cd "$(dirname "$0")" 2>/dev/null && pwd)" != "$CARTELLA" ]; then
    cp -p "$0" "$CARTELLA/aggiorna_kora.sh" 2>/dev/null
  fi
  chmod +x "$CARTELLA/setup.sh" "$CARTELLA/verifica.sh" "$CARTELLA/start.sh" \
           "$CARTELLA/ripristina.sh" "$CARTELLA/diagnostica_ai.sh" "$CARTELLA/aggiorna_kora.sh" 2>/dev/null
  return 0
}

rollback() {
  [ -n "$BACKUP_CODICE" ] && [ -f "$BACKUP_CODICE" ] || {
    echo "  Nessuna copia del codice: reinstalla il pacchetto precedente a mano."
    return 1
  }
  echo "  Ripristino il codice precedente da $BACKUP_CODICE…"
  rm -rf "$CARTELLA/kora_studio" "$CARTELLA/assets" "$CARTELLA/docs"
  tar -xzf "$BACKUP_CODICE" -C "$CARTELLA" 2>/dev/null
  echo "  Codice ripristinato. kora_data/ non è mai stato toccato."
}

if [ "$SOLO_VERIFICA" -eq 0 ]; then
  titolo "3/7" "Server"
  ferma_server

  titolo "4/7" "Pacchetto ${VERSIONE}"
  scarica_pacchetto
  SORGENTE="$(estrai_pacchetto)"

  titolo "5/7" "Aggiornamento dei file (kora_data intatta)"
  if aggiorna_file "$SORGENTE"; then
    ok "file applicazione aggiornati"
    if grep -q "version=\"${VERSIONE}\"" "$CARTELLA/kora_studio/api/app.py"; then
      ok "versione ${VERSIONE} presente in api/app.py"
    else
      ko "versione non aggiornata in api/app.py"; rollback; exit 1
    fi
    echo "$VERSIONE" > "$CARTELLA/kora_data/installed_version.txt"
  else
    ko "copia dei file non riuscita"; rollback; exit 1
  fi
fi

# --------------------------------------------------- 6. setup e verifica
titolo "6/7" "Setup, verifica e diagnostica"
cd "$CARTELLA" || { ko "cartella non accessibile"; exit 1; }

if [ "$SOLO_VERIFICA" -eq 0 ]; then
  info "setup.sh (dipendenze, FFmpeg, seed)…"
  if ! bash setup.sh >"$LAVORO/setup.log" 2>&1; then
    ko "setup fallito"; tail -20 "$LAVORO/setup.log" | sed 's/^/       /'
    rollback
    exit 1
  fi
  grep -E "Installazione precedente|Setup completato|OK  " "$LAVORO/setup.log" | tail -4 | sed 's/^/       /'
  ok "setup completato"
fi

VERIFICA_ESITO=0
bash verifica.sh >"$LAVORO/verifica.log" 2>&1 || VERIFICA_ESITO=$?
RIEPILOGO="$(grep "esiti:" "$LAVORO/verifica.log" | tail -1)"
if [ "$VERIFICA_ESITO" -eq 0 ]; then
  ok "verifica: ${RIEPILOGO:-completata}"
else
  ko "verifica con controlli falliti: ${RIEPILOGO:-vedi log}"
  grep "^KO" "$LAVORO/verifica.log" | sed 's/^/       /' | head -8
  warn "i dati sono intatti; leggi i KO sopra prima di usare KORA"
fi

if [ "$AVVIA" -eq 1 ]; then
  if [ -n "$(porta_in_uso)" ]; then
    warn "porta $PORTA già occupata: non avvio un secondo server"
  else
    info "avvio KORA sulla porta $PORTA…"
    mkdir -p "$CARTELLA/kora_data/logs"
    KORA_PORT="$PORTA" nohup bash start.sh >"$CARTELLA/kora_data/logs/server.log" 2>&1 &
    atteso=0
    while [ "$atteso" -lt 40 ]; do
      if curl -fsS "http://127.0.0.1:${PORTA}/api/health" >/dev/null 2>&1; then break; fi
      sleep 1; atteso=$((atteso + 1))
    done
    if curl -fsS "http://127.0.0.1:${PORTA}/api/health" >/dev/null 2>&1; then
      ok "server risponde: http://127.0.0.1:${PORTA}"
    else
      ko "il server non risponde: kora_data/logs/server.log"
    fi
  fi
fi

DIAG_ESITO=0
SERVER_ATTIVO=0
curl -fsS "http://127.0.0.1:${PORTA}/api/health" >/dev/null 2>&1 && SERVER_ATTIVO=1
if [ "$SERVER_ATTIVO" -eq 0 ]; then
  warn "server non attivo: diagnostica AI rimandata (avvia KORA e rilancia --solo-verifica)"
elif [ -x "$CARTELLA/diagnostica_ai.sh" ]; then
  KORA_PORT="$PORTA" bash "$CARTELLA/diagnostica_ai.sh" >"$LAVORO/diagnostica.log" 2>&1 || DIAG_ESITO=$?
  if [ "$DIAG_ESITO" -eq 0 ]; then
    ok "diagnostica AI: motore creativo operativo"
  else
    ko "diagnostica AI: motore creativo NON operativo"
    grep -E "^(  KO|Risultato)" "$LAVORO/diagnostica.log" | sed 's/^/       /' | head -6
  fi
  grep -E "provider \.|modello \.|modalità" "$LAVORO/diagnostica.log" | sed 's/^/       /'
fi

# ------------------------------------------------------------- 7. esito
titolo "7/7" "Esito"
if [ "$SOLO_VERIFICA" -eq 0 ]; then
  info "installazione: $CARTELLA"
  info "versione:      $(sed -n 's/.*version="\([0-9.]*\)".*/\1/p' kora_studio/api/app.py | head -1)"
  [ -n "$BACKUP_CODICE" ] && info "backup codice: $BACKUP_CODICE"
  [ -n "$DB_BACKUP" ] && info "backup dati:   ${DB_BACKUP#$CARTELLA/}"
fi
MODO="Aggiornamento applicato"
[ "$SOLO_VERIFICA" -eq 1 ] && MODO="Nessuna modifica (solo verifica)"
if [ "$VERIFICA_ESITO" -eq 0 ] && [ "$DIAG_ESITO" -eq 0 ]; then
  echo
  if [ "$SOLO_VERIFICA" -eq 0 ] && [ "$AVVIA" -eq 0 ]; then
    echo "  Aggiornamento completato. Avvia KORA con:  cd \"$CARTELLA\" && ./start.sh"
  else
    echo "  Tutto pronto. Apri http://127.0.0.1:${PORTA} e premi «Suggerisci un tema»."
  fi
  exit 0
fi
echo
echo "  ${MODO}, ma resta qualcosa da sistemare (vedi KO sopra)."
echo "  I tuoi dati non sono stati toccati."
if [ -n "$(porta_in_uso)" ]; then
  echo "  L'app è comunque raggiungibile su http://127.0.0.1:${PORTA}"
fi
if [ -n "$BACKUP_CODICE" ] && [ -f "$BACKUP_CODICE" ]; then
  echo "  Per tornare al codice precedente:"
  echo "    tar -xzf \"$BACKUP_CODICE\" -C \"$CARTELLA\""
fi
exit 1
