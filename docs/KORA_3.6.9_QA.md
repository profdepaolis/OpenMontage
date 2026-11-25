# KORA STUDIO LIGHT 3.6.9 — report di collaudo

Data: 25 novembre 2026 · ambiente: sandbox Linux (Python 3, ffmpeg incluso nel venv).
Correzione di 3.6.8, distribuita come aggiornamento sul posto (dati intatti).

## 1. Perché questa versione

Segnalazione dell'utente, con schermata: progetto «L'aperitivo senza fretta», job in
**Errore** con «Coerenza tra voiceover e sottotitoli: Testo e sottotitoli raccontano
cose diverse», mentre i sottotitoli erano una **scelta editoriale** dichiarata
dall'utente e la timeline era solo `estimated` (nessun motore ASR).

## 2. Cosa è stato corretto

| # | difetto | correzione |
|---|---|---|
| 1 | un sottotitolo editoriale bloccava l'esportazione senza uscita | sorgente dei sottotitoli dichiarabile per progetto (`transcript` predefinito / `editorial`); con `editorial` il controllo diventa avviso + revisione umana |
| 2 | il messaggio di blocco elencava anche i controlli non bloccanti | `_qc_block_message()` separa bloccanti e «Avvisi (non bloccanti)» |
| 3 | nessun rimedio dalla pagina Produzione | due pulsanti nel riquadro d'errore: «Allinea sottotitoli al testo», «Mantieni sottotitoli editoriali» |
| 4 | la produzione riscriveva i sottotitoli dell'utente con la trascrizione | la riscrittura avviene solo se il sottotitolo coincideva con il parlato; altrimenti viene conservato e registrato (`preserved_captions`) |
| 5 | SRT/VTT/ASS contenevano la trascrizione, non i sottotitoli mostrati | con `editorial` i cue sono costruiti dal testo delle scene (`editorial_caption_cues`) |
| 6 | produzione e QC usavano default diversi (`editorial` vs `transcript`) | entrambi leggono `caption_source_for()` (default `transcript`) |

## 3. Test automatici

    .venv/bin/python -m pytest tests/kora_studio -q

Esito: **305 passed, 3 skipped in 985,62 s**.

Nuovo file `tests/kora_studio/test_v369_editorial_captions.py` (8 test):

1. default `transcript` → blocco con **due** rimedi e numeri di scena;
2. `editorial` → superato, avviso, revisione umana, «non sono la trascrizione letterale»;
3. default `transcript` e persistenza della scelta dichiarata;
4. messaggio di blocco: solo bloccanti, avvisi in coda separata;
5. sottotitolo creativo **non** sovrascritto dalla trascrizione;
6. sottotitolo fedele che invece segue davvero il parlato;
7. cue editoriali con le parole dell'utente dentro la finestra della scena;
8. timeline costruita come editoriale che sostituisce i cue della trascrizione.

Gruppi di sincronizzazione: `test_v366` (16) + `test_v367` (16) + `test_v368` (18) +
`test_v369` (8) = **58 test**, tutti verdi.

## 4. Collaudo con produzione reale

`tools/accettazione_369.py` → esito **OK**. Artefatti in `docs/accettazione_3.6.9/`.

| passo | esito |
|---|---|
| produzione con sottotitoli fedeli (default) | job **Errore** — messaggio: causa unica + «Due rimedi» + «Avvisi (non bloccanti)» |
| dichiarazione «editoriali sintetici» e nuova produzione | job **Completato**, MP4 `prj_6de272f4a3_reel_final.mp4`, 1080×1920, H.264 + AAC 48 kHz, 21,08 s, 6 935 753 byte |
| QC dell'esportazione | `passed = true`; controllo coerenza: `severity = warning`, `requires_human_review = true` |
| sottotitoli dopo il ricalcolo | invariati |
| SRT consegnato | scene 1-3 con «Il pomeriggio rallenta.», «Ombre lunghe.», «Un bicchiere, il silenzio.» |

## 5. Interfaccia

`npx tsc --noEmit` senza errori; `npm run build` → 3 pagine statiche generate
(`out/` ricompilato e incluso nel pacchetto).

## 6. Cosa NON è stato collaudato qui

- **Clic reali sui due pulsanti** della pagina Produzione in un browser vero
  (Chrome/Safari su macOS): il codice compila e le API chiamate sono coperte dai test,
  ma il clic non è stato eseguito.
- **Il progetto dell'utente** («L'aperitivo senza fretta»): non è stato riaperto in
  questa macchina; il caso è stato riprodotto con un progetto equivalente.
- **Motore ASR reale** (`faster-whisper`): i pesi non sono scaricabili dalla sandbox,
  quindi la trascrizione vera resta da provare sul Mac con `./installa_asr.sh`.
- **Allineamento acustico forzato** (MFA/Gentle) e diarizzazione: invariati, non eseguiti.

## 7. Compatibilità e dati

- Nessuna migrazione distruttiva, nessuna modifica a `kora_data/`.
- Progetti esistenti: comportamento invariato (default `transcript`). Chi ha sottotitoli
  editoriali deve dichiararli una volta (pulsante o menu in Sincronizzazione).
- Rollback: l'aggiornamento conserva la copia del codice precedente
  (`Kora Studio Light_backup_*`) e `aggiorna_kora.sh --solo-verifica` non modifica nulla.
