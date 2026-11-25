# Installazione tecnica — KORA STUDIO LIGHT 3.6.8

Documento per chi vuole capire cosa fa il doppio clic, o riparare un guasto.
Per l'uso normale basta `LEGGIMI.txt`.

---

## 1. Percorso breve (consigliato)

```bash
cd "$HOME/Documenti/Kora Studio Light"
./setup.sh && ./verifica.sh && ./start.sh
```

Poi apri `http://127.0.0.1:8020`.

## 1b. Aggiornamento dalla 3.6.0

1. backup (Impostazioni › Backup, oppure `curl -X POST http://127.0.0.1:8020/api/backup/create`);
2. chiudi KORA e sostituisci i file dell'applicazione **senza toccare `kora_data/`**;
3. `./setup.sh`: riconosce la versione precedente e passa a 3.6.1 con copia di
   sicurezza del database;
4. `./diagnostica_ai.sh`: conferma che provider e modello attivi sono quelli del
   Model Manager (HY4 Flash). Se compare ancora DeepSeek: `./diagnostica_ai.sh --fix`.

Dettagli: `docs/KORA_3.6.1_RIPRISTINO_AI.md`.

### Aggiornamento in un solo comando

```bash
curl -fsSL "https://drive.google.com/uc?export=download&id=1kqYDia24U0Xf1iZDjrIsaJizfpWKChVx" \
  -o /tmp/aggiorna_kora.sh && bash /tmp/aggiorna_kora.sh
```

Lo script `aggiorna_kora.sh` fa tutto, in ordine, e non cancella nulla:

1. trova l'installazione (`~/Documenti/Kora Studio Light`, `~/Documents/…`, Desktop,
   `/Applications`, oppure `--cartella`); se non esiste la crea;
2. backup: copia del database in `kora_data/backups/pre_3.6.1_<data>.db` + archivio
   del codice attuale in `~/KORA_backup_app_<data>.tar.gz`;
3. ferma il server sulla porta 8020 (o `KORA_PORT`), senza lasciare processi orfani;
4. scarica il pacchetto 3.6.1 (`id=1t0HRcoBl4SnQ0uajqtkfVBW9au_l3KCR`) e ne verifica
   l'integrità; in alternativa `--da-zip <file>` o `KORA_ZIP_URL=<url>`;
5. sostituisce **solo** applicazione, asset, docs e demo: `kora_data/` (foto, progetti,
   database, voiceover, loghi, Reel) non viene mai toccata né cancellata;
6. esegue `setup.sh`, `verifica.sh`, avvia il server e lancia `diagnostica_ai.sh`;
7. se la versione non risulta 3.6.1 o il setup fallisce, **rimette il codice
   precedente** dall'archivio del punto 2 (i dati restano intatti).

Opzioni: `--cartella DIR`, `--da-zip FILE`, `--porta 8021`, `--no-avvio`,
`--solo-verifica` (nessuna modifica), `KORA_ZIP_URL=<url>`.

Esito: codice 0 solo se verifica e diagnostica AI sono entrambe a posto. Su un Mac
senza OpenCode la diagnostica chiude con «CLI opencode non trovata nel PATH»
(codice 1): è il comportamento corretto, non un guasto dell'aggiornamento.

Prove eseguite su questo script (sandbox, installazione 3.6.0 simulata con foto e
progetto dell'utente): backup creato, 41 file del Vault intatti, progetto intatto,
versione 3.6.1, `verifica.sh` 36 OK · 0 KO · 1 avviso, server avviato e rispondente,
diagnostica con esito onesto, stop del server via `pgrep` quando `lsof` non c'è,
download fallito gestito con messaggio e rimedio, `--solo-verifica` senza modifiche.


## 1a. Motore di trascrizione (3.6.7, facoltativo)

    ./installa_asr.sh --verifica     # controlla architettura, Python, RAM, spazio
    ./installa_asr.sh                # installa faster-whisper + modello, prova reale
    ./installa_asr.sh --livello rapido|bilanciato|accurato

Installa solo dentro `.venv` di KORA, nessun servizio cloud, nessun costo.
Se non lo installi, KORA non inventa la trascrizione: usa il copione o la stima.

## 1a-bis. Cosa è cambiato nella 3.6.8 (correzione)

- `mode="script"` (Allinea un copione) ora **trascrive** l'audio e allinea il
  copione ai tempi riconosciuti; le parole non riconosciute sono elencate
  (`mismatches`) e mostrate nel pannello.
- Senza motore ASR la modalità copione dichiara `alignment="estimated"`: non
  viene più presentata una distribuzione proporzionale come allineamento.
- `caption_timings_from_transcript()` usa **tutti** i segmenti della scena.
- `verified=True` solo con parole provenienti dall'ASR; un copione allineato ha
  `alignment="forced_aligned"`, `verified=false`, `verification.*` distinti.
- `./verifica_test.sh --veloce` esegue solo i test di sincronizzazione.

Dettagli e prove: `docs/KORA_3.6.8_SINCRONIZZAZIONE_REGRESSIONI.md`.

## 1b. Sincronizzazione voce/scene/sottotitoli (3.6.6 → 3.6.8)

La timeline canonica sta in `kora_data/projects/<id>/project_timeline.json`.
Diagnostica rapida:

    ./diagnostica_sync.sh              # tutti i progetti
    ./verifica_test.sh --veloce        # test della sincronizzazione
    ./diagnostica_sync.sh prj_xxxx     # un progetto

Ricalcolo e verifica:

    curl -s -X POST http://127.0.0.1:8020/api/projects/ID/timeline/rebuild -H 'Content-Type: application/json' -d '{}'
    curl -s -X POST http://127.0.0.1:8020/api/projects/ID/sync/verify

Se compare «voce_tagliata», la voce finirebbe oltre la fine del Reel: allunga
il Reel, accorcia il copione o riduci le pause eliminabili (KORA non tronca).
Dettagli: `docs/KORA_3.6.6_SINCRONIZZAZIONE.md`.

## 1b-bis. Esportazione bloccata da «Coerenza tra voiceover e sottotitoli» (3.6.4 → 3.6.5)

Da 3.6.5 un sottotitolo che **riformula** la frase con le parole dello
speaker (accorciandola, o cambiando articoli e preposizioni) è un avviso,
non un errore: contano le parole portanti, non la forma esatta. Il blocco
resta quando il sottotitolo parla d'altro.

Da 3.6.4 un sottotitolo più sintetico del testo detto non blocca più
l'esportazione: viene registrato come avviso con revisione consigliata.
Il blocco resta solo quando i due testi divergono davvero; in quel caso il
messaggio indica le scene e il rimedio:

    curl -s -X POST http://127.0.0.1:8020/api/projects/PROJECT_ID/captions/align

oppure, nell'interfaccia: Storyboard → «Allinea sottotitoli», poi
Production → Riprova. Voce, copione e clip in cache non vengono rigenerati.
Dettagli: `docs/KORA_3.6.4_SOTTOTITOLI.md`.

## 1c. Rendering lento o «timed out» (3.6.3)

Dal 3.6.3 il limite di tempo del montaggio è proporzionale ai frame
(`max(600 s, frame × 1.6)`) e i sottotitoli vengono decodificati solo per il
loro intervallo. Se una macchina resta comunque troppo lenta:

```bash
KORA_RENDER_TIMEOUT_SEC=2400 KORA_X264_PRESET=ultrafast ./start.sh
```

- `KORA_RENDER_TIMEOUT_SEC` — secondi massimi per il solo montaggio (default automatico);
- `KORA_X264_PRESET` — `veryfast` (default), `superfast`, `ultrafast`, `fast`, `medium`
  (più lento = file più piccoli a parità di qualità).

Dopo un errore di rendering le scene restano in cache: **Riprova** ripete solo il
montaggio. Dettagli: `docs/KORA_3.6.3_RENDER_ROBUSTO.md`.

## 2. Cosa fa `setup.sh`

1. controlla Python (3.9+, consigliato 3.11) e crea `.venv`;
2. installa le dipendenze da `kora_studio/requirements_kora.txt` (wheel precompilate);
3. verifica **FFmpeg** e **FFprobe** eseguendo `-version` (non solo guardando il file):
   - usa il binario di sistema se funziona, altrimenti `imageio-ffmpeg`, altrimenti
     `ffprobe-static` installato via npm, altrimenti `static-ffmpeg`;
   - **non collega mai** un binario di un'altra architettura: è la causa storica
     dell'errore `Exec format error` all'avvio;
4. l'interfaccia è già compilata in `kora_studio/frontend/out` (nessuna build richiesta);
   se manca e c'è Node, prova a compilarla;
5. esegue `seed` (Brand Brain, Media Vault, Sound Library) e `doctor`.

## 3. Cosa controlla `verifica.sh` (37 controlli)

Ambiente Python e moduli · FFmpeg/FFprobe realmente eseguibili · filtri `zoompan`,
`overlay` ed encoder `libx264` · PyAV · interfaccia compilata e suo peso ·
import degli agenti v3.5 e del Model Manager 3.6 · stati QC · blocchi di regia ·
script di ripristino · master audio a −14 LUFS · costruzione dell'app · CLI ·
motore di movimento · logo e font KORA · database e cartelle scrivibili ·
spazio disco · porta libera · versione 3.6.

Esito atteso: `37 OK · 0 KO · 0 avvisi` con il server fermo
(`35 OK · 1 avviso` se il server è già attivo: la porta risulta occupata,
non è un guasto).

## 4. Tabella guasti

| Sintomo | Causa | Rimedio |
|---|---|---|
| `Exec format error` su ffprobe | binario di un'altra architettura collegato in `.venv/bin` | `rm .venv/bin/ffprobe && ./setup.sh` |
| `FFprobe assente` | né sistema né pacchetti disponibili | `brew install ffmpeg` |
| Interfaccia bianca o 404 su `/` | `frontend/out` mancante | `./setup.sh` (compila se c'è Node) o reinstalla il pacchetto |
| `Porta 8020 già occupata` | altra istanza aperta | `KORA_PORT=8021 ./start.sh` |
| Il browser non si apre | `open` bloccato | apri a mano `http://127.0.0.1:8020` |
| Reel senza voce | nessun provider TTS attivo | normale: il Reel esce con musica, SFX e sottotitoli |

Log utili: `~/.kora/logs/errors.log`, `/tmp/kora_pip.log`, `/tmp/kora_frontend.log`,
`/tmp/kora_seed.log`, `/tmp/kora_doctor.log`.

## 5. Backup e ripristino

```bash
./.venv/bin/python -m kora_studio backup   # crea un backup
./ripristina.sh                            # elenca i backup disponibili
./ripristina.sh --ultimo                   # ripristina il più recente (chiede conferma)
```

Il backup contiene database, progetti, export e originali del Media Vault.
Il ripristino mappa le cartelle corrette (`originals/` → `kora_data/vault/originals/`),
rifiuta percorsi non sicuri e crea **prima** un backup di sicurezza dello stato
attuale: l'operazione è annullabile. Con KORA in esecuzione lo script si ferma
e chiede di chiudere l'app.

## 6. Disinstallazione

Basta cancellare la cartella del programma. I dati stanno in `~/.kora`:
cancella anche quella solo se vuoi perdere progetti e materiali importati.

## 7. Prove eseguite su questa build

- `./setup.sh` in cartella pulita → completato senza errori;
- `./verifica.sh` → 38 controlli (37 + diagnostica motori 3.6.2), esito nel report;
- `pytest tests/kora_studio` → suite completa verde, incluso il nuovo
  `test_v36_model_manager.py` (19 verifiche) e `test_v36_consolidation.py`;
- produzione completa di Reel 20 s (500 frame) e 30 s (750 frame), QC reale
  con stati a quattro valori (`demo/`);
- OpenCode non è installato in questo ambiente di collaudo: tutto ciò che
  dipende dal CLI è dichiarato **non collaudato**, non simulato.
