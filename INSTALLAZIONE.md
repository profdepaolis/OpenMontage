# Installazione tecnica — KORA STUDIO LIGHT 3.6.1

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
- `./verifica.sh` → **37 OK · 0 KO · 0 avvisi** (server fermo);
- `pytest tests/kora_studio` → suite completa verde, incluso il nuovo
  `test_v36_model_manager.py` (19 verifiche) e `test_v36_consolidation.py`;
- produzione completa di Reel 20 s (500 frame) e 30 s (750 frame), QC reale
  con stati a quattro valori (`demo/`);
- OpenCode non è installato in questo ambiente di collaudo: tutto ciò che
  dipende dal CLI è dichiarato **non collaudato**, non simulato.
