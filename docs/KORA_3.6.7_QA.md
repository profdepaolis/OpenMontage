# KORA STUDIO LIGHT 3.6.7 — Rapporto di collaudo (QA)

Data: 23 novembre 2026 · base: `KORA_Studio_Light_3.6.6_installer.zip` · build Linux (sandbox di sviluppo), frontend compilato.

Questo rapporto dichiara **solo ciò che è stato eseguito davvero**. Tutto ciò che non è stato
eseguito è elencato in §7 «Non collaudato» e non va considerato verificato.

---

## 1. Consegna

| Artefatto | Valore |
|---|---|
| Installer | `KORA_Studio_Light_3.6.7_installer.zip` (link Drive e md5 nella consegna) |
| Contenuto | 445 file, circa 68,1 MB decompressi (frontend `out/` compilato incluso, `tests/` completo) |
| Aggiornatore | `aggiorna_kora_3.6.7.sh.txt` (id e md5 nella consegna) |

Comando unico per aggiornare sul Mac, senza spostare file a mano (l'id dell'aggiornatore è
quello comunicato nella consegna):

```bash
curl -fsSL "https://drive.google.com/uc?export=download&id=<ID_AGGIORNATORE>" \
  -o /tmp/aggiorna_kora.sh && bash /tmp/aggiorna_kora.sh
```

Verifica dell'archivio scaricato: `md5 -q KORA_Studio_Light_3.6.7_installer.zip` deve dare il
valore indicato nella consegna; in alternativa `bash /tmp/aggiorna_kora.sh --da-zip <file>`
rifiuta archivi non validi prima di toccare l'installazione.

L'aggiornatore: backup del database e del codice, ferma il server, scarica lo ZIP 3.6.7,
verifica l'archivio, sostituisce **solo** `kora_studio/`, `assets/`, `docs/`, `tests/` e gli
script di primo livello, esegue `setup.sh`, `verifica.sh`, riavvia e lancia la diagnostica AI.
Se qualcosa fallisce ripristina il codice precedente. `kora_data/` (database, progetti,
Media Vault, voci, loghi, Reel) non viene **mai** toccata.

Rollback manuale: `tar -xzf ~/KORA_backup_app_<data>.tar.gz -C "$HOME/Documents/Kora Studio Light"`.

---

## 2. Cosa è cambiato rispetto alla 3.6.6

Nessuna funzione è stata rimossa. Dettaglio tecnico completo in
`docs/KORA_3.6.7_ALLINEAMENTO.md`; sintesi dei difetti chiusi:

| # | Difetto 3.6.6 | Correzione 3.6.7 |
|---|---|---|
| 1 | il renderer mostrava `scene.subtitle_text` anche quando il transcript diceva altro | `_caption_timings()` usa `caption_timings_from_transcript()`: i tempi (e le parole) vengono dal segmento del transcript, non dall'indice della scena |
| 2 | nessun allineamento forzato per MP3 + copione | `align_script_to_asr()` (SequenceMatcher su token normalizzati): blocchi uguali ereditano i tempi ASR, i blocchi diversi vengono distribuiti nell'intervallo ASR, le discordanze sono elencate in `mismatches` |
| 3 | `start_offset_sec` applicato solo parzialmente | offset applicato a parole, segmenti, cue, `speech_regions` e frame, prima dell'assegnazione degli `word_id` |
| 4 | la UI non distingueva ASR / copione / stima | `SyncPanel` ha la scelta esplicita «Trascrivi con ASR / Allinea il copione / Stima», più la sorgente sottotitoli (transcript / editoriale) |
| 5 | l'API accettava qualunque `mode` | `mode` validato in `{asr, auto, script, estimated}`, altrimenti 400 |
| 6 | la trascrizione non si propagava alle scene | `apply_transcript_to_scenes()` aggiorna testi e cue, **salta le scene con `locks`** |
| 7 | il QC non verificava la provenienza dei sottotitoli | nuovo controllo «Sottotitoli coerenti con la revisione del transcript» (conta cue fedeli vs `editorial_in_segment`) |
| 8 | l'installer 3.6.6 non conteneva i test né `diagnostica_sync.sh` | `tools/pack_kora.py` copia `tests/` e **rifiuta** di creare il pacchetto se mancano `diagnostica_sync.sh`, `installa_asr.sh`, `verifica_test.sh`, `requirements-dev.txt` |

Provenienza dei tempi dichiarata in ogni artefatto: `asr` (verified solo con timestamp di
parola), `forced_aligned`, `estimated`, `manual`, più `editorial_in_segment` per i sottotitoli
editoriali. Nulla di stimato viene etichettato come verificato.

---

## 3. Collaudo automatico — eseguito

Comando: `.venv/bin/python -m pytest tests/kora_studio -q` (Linux, Python 3.11, FFmpeg del venv).

```
279 passed, 3 skipped in 910.10s (0:15:10)
```

I 3 skip sono test opzionali già presenti nelle versioni precedenti (dipendono da binari
esterni non installati nella sandbox: Remotion/Chromium).

Suite specifiche della sincronizzazione:

| File | Esito |
|---|---|
| `tests/kora_studio/test_v367_speech_sync.py` | **16 passed** in 18,37 s |
| `tests/kora_studio/test_v366_audio_timeline.py` | **16 passed** in 21,25 s |

Copertura reale dei 16 test 3.6.7 (nessun mock della trascrizione: dove serve un risultato ASR,
è fornito come *input dichiarato*):

1. offset 0 / 100 / 250 / 500 ms applicato a parole, segmenti, cue, `speech_regions` e frame;
2. offset oltre la fine del reel → problema `voce_tagliata` dichiarato, non nascosto;
3. forced alignment su copione noto: 7 parole, `timing_source == forced_aligned`, tempi ereditati dall'ASR;
4. forced alignment con parola diversa (`dicembre` vs `novembre`): discordanza elencata;
5. forced alignment non è trascrizione: `verified == False`, `alignment == estimated` quando non c'è ASR;
6. sottotitoli fedeli: 1:1 con le parole del segmento (stesso numero di parole, primo tempo identico);
7. sottotitoli editoriali marcati `editorial_in_segment`;
8. propagazione del transcript alle scene, scene bloccate intatte;
9. API: `mode` non valido → 400, `mode` validi accettati;
10. api/web: `caption_source` propagato alla preferenza di progetto.

### Prova di avvio del pacchetto (non del repository)

Pacchetto estratto in `/tmp/pkg367/Kora Studio Light`, avviato con il Python del venv e
`kora_data` nuovo:

```
GET /api/health      -> 200 (ok:true, ffmpeg e ffprobe rilevati, db_schema_version 4, 5 preset)
GET /openapi.json    -> info.version == "3.6.7"
POST /api/projects/<inesistente>/timeline/rebuild -> 404
```

Il pacchetto parte quindi con i file effettivamente spediti (frontend compilato incluso).
`installa_asr.sh --verifica` è stato eseguito: rileva architettura, Python 3.11 di KORA,
spazio su disco, e **non** installa nulla senza `--livello`.

---

## 4. Frontend

`npx tsc --noEmit` pulito; `npm run build` (Next.js 16.4.6, Turbopack): compilazione ok,
3 pagine statiche generate, output in `kora_studio/frontend/out/` — è quello che viaggia nello ZIP.

---

## 5. Controlli di non regressione

* Suite completa verde (279 test) ⇒ pipeline creative, Media Vault, storyboard, Script/Visual
  Director, Caption Designer, HyperFrames, Remotion (quando disponibile), FFmpeg, Voice Studio,
  ElevenLabs/Edge/Piper/Chatterbox/Qwen3, OpenCode, Model Manager, revisioni selettive, lock di
  scena, backup, controllo costi e archivio progetti invariati.
* Nessuna modifica cosmetica non richiesta: le uniche novità visive sono i due selettori in
  `SyncPanel` (modalità di allineamento e sorgente sottotitoli).
* Nessuna voce di menu rimossa; `kora_data/` non è toccata né dall'installer né dall'aggiornatore.

---

## 6. Come rifare il collaudo sul Mac

```bash
cd "$HOME/Documents/Kora Studio Light"
./verifica_test.sh          # installa pytest/httpx nel venv di KORA ed esegue la suite
./diagnostica_sync.sh       # stato ASR, modelli, timeline, artefatti di sincronizzazione
./installa_asr.sh --verifica
```

`verifica_test.sh` richiede rete solo per `pip install -r requirements-dev.txt`
(pytest, httpx): nessun modello viene scaricato.

---

## 7. Non collaudato (dichiarato, non simulato)

Questi punti **non** sono stati eseguiti in questa sessione e restano da verificare sul tuo Mac:

1. **Trascrizione ASR reale** (faster-whisper / Whisper): in sandbox `huggingface.co` è
   irraggiungibile (errore SSL), quindi nessun peso di modello può essere scaricato. Il codice
   ASR, `installa_asr.sh` e il percorso `mode=asr` sono scritti e testati con risultati ASR
   forniti come input, ma **nessuna trascrizione audio→testo è stata eseguita davvero** qui.
2. **Installazione su macOS reale** (Intel e Apple Silicon), inclusi i livelli
   `rapido` / `bilanciato` / `accurato` di `installa_asr.sh`.
3. **Allineatore acustico** (MFA / Gentle): il forced alignment implementato è testuale
   (confronto token script↔ASR), non acustico.
4. **Diarizzazione** (speaker) e editor multi-traccia della forma d'onda.
5. **Roll §14 A / B / C** (reel interni 20 s, MP3+copione, MP3→STT→correzione→reel) con
   verifica a campione delle parole sul file prodotto.
6. **Interazione nel browser** con i nuovi controlli di `SyncPanel` (click reali, non test API).

Nessuno di questi punti è stato sostituito da una simulazione: dove manca il motore, il sistema
lo dichiara. Il pannello Sync mostra l'avviso «Nessun motore ASR installato» (stato da
`GET /api/asr/status`) e propone le alternative; la timeline dichiara
`transcript.alignment = "estimated"` e `verified = false`, e il QC non conta i tempi stimati
come verificati.

---

## 8. Note operative

* Nella cartella di export di ogni progetto vengono scritti gli artefatti di sincronizzazione
  `<id>_cues.json`, `<id>_sottotitoli.srt`, `<id>_sottotitoli.vtt`, `<id>_sottotitoli.ass`,
  `<id>_timeline.json` (timeline canonica) e `<id>_transcript.json`, accanto a
  `<id>_report_tecnico.json|md`. Il QC verifica che i sottotitoli provengano dalla revisione
  approvata del transcript e che i tempi dichiarati non siano etichettati come verificati se
  sono stimati.
* Il preferito `caption_source_<project_id>` decide se i sottotitoli seguono il transcript
  (`transcript`, default per audio importato) o restano editoriali (`editorial`).
* Le scene con `locks` non vengono mai riscritte automaticamente dal transcript.
