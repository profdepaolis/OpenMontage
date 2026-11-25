# KORA STUDIO LIGHT 3.6.9 — sottotitoli editoriali e messaggio di blocco

**Tipo**: correzione di 3.6.8 (aggiornamento sul posto, dati intatti).
**Origine**: segnalazione dell'utente con schermata di produzione reale.

## 1. Il difetto, come si è presentato

Progetto «L'aperitivo senza fretta» (v8), job `JOB_F48CC3787B` in stato **Errore**.
Tutti i passaggi verdi fino al Rendering, fase QC bloccata con un solo messaggio:

> Coerenza tra voiceover e sottotitoli: Testo e sottotitoli raccontano cose diverse
> (scena 1: voce «Nel tardo pomeriggio d'autunno, la luce si abba…» ≠ sottotitoli
> «Il pomeriggio rallenta.»)

Nello stesso messaggio comparivano anche controlli **non** bloccanti (trascrizione non
verificata, tempi `estimated`), mescolati alla causa reale.

Tre problemi distinti:

1. **Il QC trattava una scelta creativa come un errore tecnico.** L'utente aveva
   scritto sottotitoli editoriali (sintesi breve e «poetica») sotto una voce che legge
   la frase completa. È una scelta legittima e diffusa nei Reel, ma per KORA era
   indistinguibile da un sottotitolo sbagliato.
2. **Nessuna via d'uscita visibile.** Il messaggio diceva cosa non andava, non come
   uscirne dalla schermata in cui l'utente si trovava.
3. **Il messaggio di blocco elencava anche i controlli non bloccanti**, facendo
   sembrare che la trascrizione stimata fosse la causa dello stop.

## 2. Cosa è cambiato

### 2.1 La sorgente dei sottotitoli è una dichiarazione esplicita

Nuova preferenza di progetto `caption_source_<id>`:

| valore | significato | effetto sul QC |
|---|---|---|
| `transcript` (predefinito) | i sottotitoli sono il testo pronunciato | testo diverso = **errore bloccante** |
| `editorial` | i sottotitoli sono una sintesi creativa dichiarata | **avviso** + revisione umana, export consentito |

Il predefinito resta severo: nessun progetto esistente cambia comportamento da solo.
La scelta si esprime in tre modi, tutti equivalenti:

- **Produzione** → riquadro d'errore → pulsante *Editoriali sintetici*;
- **Storyboard → Trascrizione & Sincronizzazione** → menu «Testo dei sottotitoli»;
- API: `POST /api/projects/<id>/timeline/rebuild` con `{"caption_source": "editorial"}`.

Quando è dichiarato `editorial`, il ricalcolo della timeline **non riscrive** i
sottotitoli dell'utente: li lascia come sono e aggiorna solo i tempi.

### 2.2 Il controllo «Coerenza tra voiceover e sottotitoli»

- Sottotitoli editoriali dichiarati → `passed=True`, `severity="warning"`,
  `requires_human_review=True`; il dettaglio dice a chiare lettere che **non sono la
  trascrizione letterale** e come tornare ai sottotitoli fedeli.
- Sottotitoli non dichiarati e diversi → errore, con **due** rimedi espliciti
  (Allinea sottotitoli al testo / Editoriali sintetici) e i numeri di scena.
- Sottotitoli più brevi della frase (sintesi dentro la stessa scena) → come in 3.6.4,
  avviso non bloccante.

### 2.3 Il messaggio di blocco

`_qc_block_message()` ora costruisce due gruppi separati:

    Esportazione bloccata dal Controllo Qualità: <solo i controlli che bloccano>
      — Avvisi (non bloccanti): <gli altri, es. trascrizione non verificata>

Se non c'è nulla che blocca, il testo lo dice invece di elencare controlli superati.

### 2.4 Dalla pagina Produzione

Nel riquadro d'errore, quando il messaggio riguarda i sottotitoli, compaiono due
pulsanti che eseguono l'azione e riportano l'esito in linea:

- **Allinea sottotitoli al testo** → `POST /api/projects/<id>/captions/align`;
- **Editoriali sintetici** → ricalcolo timeline con `caption_source=editorial`.

Nessuna nuova schermata, nessuna modifica al resto dell'interfaccia.

### 2.5 Due difetti trovati collaudando (e corretti)

Eseguendo davvero la produzione è emerso che il blocco del QC era solo la punta:

1. **La produzione riscriveva i sottotitoli dell'utente.** `apply_transcript_to_scenes`
   in modalità «fedeli» sostituiva il testo di ogni scena con quello della trascrizione
   *prima* del controllo qualità — quindi il QC giudicava un testo già alterato e, se la
   scena era bloccata, il testo dell'utente era perso. Ora la riscrittura avviene solo
   se il sottotitolo coincideva con il parlato (o era vuoto); un sottotitolo diverso
   viene **conservato** e segnalato (`preserved_captions`, voce nel log del job).
2. **I file consegnati non contenevano i sottotitoli del Reel.** SRT/VTT/ASS/cue
   venivano dalla trascrizione, mentre a schermo si vedeva il testo editoriale. Con
   `caption_source=editorial` i cue sono ora costruiti dal testo delle scene
   (`editorial_caption_cues`, `timing_source="editorial"`) dentro la finestra della
   scena: stesse parole a schermo e nei file.
3. **Due default diversi per lo stesso progetto.** In produzione il default era
   `editorial`, nel QC `transcript`: lo stesso progetto veniva giudicato con due regole
   diverse. Ora entrambi leggono `caption_source_for()` (default `transcript`).

## 3. File toccati

| file | modifica |
|---|---|
| `kora_studio/agents/quality_controller.py` | `inspect_reel(..., caption_source="")`; ramo editoriale; rimedi nel messaggio d'errore |
| `kora_studio/agents/export_manager.py` | `export_bundle(..., caption_source="")`; `_qc_block_message()` con bloccanti/avvisi separati |
| `kora_studio/agents/director.py` | `caption_source_for(project_id)` + passaggio al QC in produzione |
| `kora_studio/frontend/src/components/ProductionView.tsx` | due pulsanti di rimedio nel riquadro d'errore |
| `kora_studio/frontend/out/` | build statica ricompilata (3.6.9) |
| `kora_studio/agents/audio_timeline.py` | `editorial_caption_cues()`: cue dei sottotitoli editoriali dentro la finestra della scena |
| `tests/kora_studio/test_v369_editorial_captions.py` | 8 test di regressione |
| `tools/accettazione_369.py`, `docs/accettazione_3.6.9/` | collaudo con due produzioni reali e MP4 |

## 4. Prove eseguite (sandbox Linux)

### 4.1 Produzione reale, due volte (non simulata)

`tools/accettazione_369.py` → **esito OK**, artefatti in `docs/accettazione_3.6.9/`:

| passo | risultato |
|---|---|
| produzione con sottotitoli «fedeli» (default) | job **Errore**, messaggio con la sola causa bloccante, i due rimedi e gli avvisi separati |
| dichiarazione «editoriali sintetici», nuova produzione | job **Completato**, MP4 1080×1920 H.264 + AAC 21,08 s, QC superato con **avviso** + revisione umana |
| sottotitoli dell'utente dopo il ricalcolo | **intatti** |
| SRT consegnato | scene 1-3 con il testo dell'utente, non con la trascrizione |

Il caso riprodotto è quello segnalato: voce che legge una frase completa, sottotitoli
brevi; timeline `none` perché su questa macchina non c'è il motore ASR.

### 4.2 Test

- `tests/kora_studio/test_v369_editorial_captions.py` → **8 passed**; insieme a
  `test_v364_caption_coherence.py`, `test_v367_speech_sync.py` e
  `test_v368_sync_regression.py` → **54 passed**.
- I test nuovi coprono: il caso reale con `transcript` → blocco con due rimedi e numeri
  di scena; lo stesso caso con `editorial` → superato con avviso e «non sono la
  trascrizione letterale»; default e persistenza della scelta; messaggio di blocco che
  tiene fuori gli avvisi; sottotitolo creativo non sovrascritto dalla trascrizione;
  sottotitolo fedele che invece segue il parlato; cue editoriali con le parole
  dell'utente dentro la finestra della scena; timeline costruita come editoriale che
  sostituisce i cue della trascrizione.
- `npx tsc --noEmit` → nessun errore; `npm run build` → 3 pagine statiche generate.
- Suite completa: esito in `docs/KORA_3.6.9_QA.md`.

## 5. Cosa non è stato collaudato qui

- **Clic reali sui due pulsanti nel browser del Mac** (Chrome/Safari): il codice
  compila e le chiamate API sono quelle già coperte dai test, ma il clic non è stato
  eseguito da un browser vero.
- **Il Reel dell'utente** («L'aperitivo senza fretta»): il progetto è sul suo Mac e
  non è stato riaperto qui; il rimedio è stato riprodotto con scene sintetiche.
- **Motore ASR reale** (`faster-whisper`): i pesi non sono scaricabili dalla sandbox,
  quindi la trascrizione vera resta da provare sul Mac con `./installa_asr.sh`.
