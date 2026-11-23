# KORA STUDIO LIGHT 3.6.7 — Allineamento e integrazione dei sottotitoli

Data: 2026-11-23 · Branch `arena/36db4987-openmontage` · Base: 3.6.6

## Le criticità del report 3.6.6 e cosa è stato fatto

| Criticità | Stato in 3.6.7 |
|---|---|
| il pulsante «Ricalcola allineamento» non chiedeva mai l'ASR (`use_asr` assente → false) | l'interfaccia ha tre modalità esplicite: **trascrivi**, **allinea copione**, **stima**; `use_asr` viene inviato come `true` solo quando scegli di trascrivere |
| nessun controllo di compatibilità per installare l'ASR | nuovo `installa_asr.sh`: architettura, Python di KORA, RAM, spazio, modello per livello; installa solo in `.venv`, prova reale con timestamp |
| MP3 senza copione → testi vecchi conservati | con ASR disponibile si trascrive, si mostrano le parole e si chiede revisione; senza ASR lo stato è dichiarato (`estimated`) e il testo viene marcato come **non proveniente dalla registrazione** |
| nessun forced alignment | `align_script_to_asr()`: il copione noto viene mappato sui tempi riconosciuti, con `timing_source="forced_aligned"` e l'elenco delle **discordanze** |
| etichette temporali indistinte | quattro provenienze: `asr`, `forced_aligned`, `estimated`, `manual`; `verified` è `true` **solo** per `asr` con timestamp delle parole |
| il renderer usava `scene.subtitle_text` con rimappatura per indice | i sottotitoli fedeli usano il **segmento** corrispondente (1:1 con le sue parole); la modalità editoriale è dichiarata `editorial_in_segment` |
| offset applicato all'audio ma non ai tempi | `_shift_words()` sposta parole, segmenti, cue, regioni e frame dello stesso offset; test su 0/100/250/500 ms |
| modifiche al transcript non propagate | `apply_transcript_to_scenes()` aggiorna testo e sottotitoli delle scene **non bloccate**, conserva audio e foto, invalida solo la preview |
| pacchetto senza `tests/` e senza `diagnostica_sync.sh` | il pack ora copre `tests/`, `diagnostica_sync.sh`, `installa_asr.sh`, `verifica_test.sh`, `requirements-dev.txt`; il controllo duro fallisce se mancano |

## Tre modalità di temporizzazione (interfaccia)

Storyboard → *Trascrizione & Sincronizzazione*:

1. **Trascrivi automaticamente l'audio (ASR)** → `mode="asr"`, `use_asr=true`.
   Senza motore installato il pannello lo dice e propone le alternative; non
   viene mai simulata una trascrizione.
2. **Allinea un copione già disponibile** → `mode="script"` con il testo
   incollato: se l'ASR c'è, fa forced alignment; se non c'è, resta una
   distribuzione dichiarata `estimated`.
3. **Tempi stimati dentro le pause** → `mode="estimated"`, nessuna pretesa di
   trascrizione.

Accanto: **testo dei sottotitoli** = *fedeli alla trascrizione approvata*
(predefinito per un MP3 esterno) oppure *editoriali sintetici*.

## Cosa significa "verificato"

| Provenienza | `verified` | Significato |
|---|---|---|
| `asr` con word timestamps | **true** | le parole vengono dalla voce |
| `forced_aligned` | false | il copione è mappato sui tempi ASR, ma il testo è quello scritto |
| `estimated` | false | tempi distribuiti nelle pause misurate |
| `manual` | false | testo corretto da una persona |

## QC: controlli separati

1. Voce non tagliata (errore bloccante se la voce esce dal Reel).
2. Sincronizzazione audio/scene/sottotitoli (cue fuori scena = errore).
3. **Sottotitoli coerenti con la revisione del transcript** (nuovo): i cue
   fedeli devono contenere solo parole presenti nella trascrizione; gli
   editoriali vengono contati e dichiarati.
4. Trascrizione verificata sulle parole pronunciate (`non_verificato` se
   stimata o corretta a mano).

`ok=true` nello `/sync/verify` significa *nessun conflitto strutturale*: non
è la stessa cosa di sincronizzazione fonetica verificata, che richiede ASR.

## Riproducibilità dei test (criticità 13)

Nel pacchetto ora ci sono:

- `tests/` completo (suite, fixture, conftest);
- `verifica_test.sh` (suite completa o `--veloce` per la sincronizzazione);
- `diagnostica_sync.sh` (stato di tutti i progetti via API);
- `requirements-dev.txt` (`pytest`, `httpx`);
- questo documento e il report QA.

```bash
./verifica_test.sh --veloce    # 32 test della sincronizzazione
./verifica_test.sh             # suite completa
./diagnostica_sync.sh
```

## Collaudo eseguito (Linux, questo ambiente)

| Prova | Esito |
|---|---|
| `tests/kora_studio/test_v367_speech_sync.py` | **16 passed** |
| `tests/kora_studio/test_v366_audio_timeline.py` | **16 passed** |
| Suite completa | vedi `/tmp/pytest_367.log` (riportata nel report QA) |
| Offset 0 / 100 / 250 / 500 ms | parole, segmenti, cue, regioni e frame spostati in modo coerente |
| Forced alignment su copione con una parola sbagliata | 7 parole allineate, `dicembre` segnalata come discordanza |
| Sottotitoli fedeli | tempi presi dal segmento, corrispondenza 1:1 |
| Sottotitoli editoriali | marcati `editorial_in_segment` |
| Blocco sottotitoli | la scena bloccata viene saltata e riportata in `skipped` |
| API | `mode` non valido → 400; `mode="asr"` senza motore → nota «ASR non disponibile» e nessun testo inventato |

## Non collaudato — dichiarato

- **Trascrizione reale**: in questo ambiente `huggingface.co` non è
  raggiungibile, quindi i pesi dei modelli non si possono scaricare. Il codice
  ASR è completo e l'adapter è testato, ma **nessuna trascrizione vera è stata
  eseguita qui**. La prova sul tuo Mac la fa `./installa_asr.sh`, che termina
  con una trascrizione di prova e stampa i timestamp.
- **Forced alignment acustico** (tipo MFA): non implementato; l'allineamento
  usa i tempi ASR.
- **Diarizzazione**: assente.
- **Editor multitraccia con waveform**: il pannello mostra frasi, tempi e
  correzioni, non uno scrubbing audio.
- **macOS Intel**: le prove girano su questa macchina Linux.

## Comandi

```bash
./installa_asr.sh --verifica          # compatibilità, nessuna installazione
./installa_asr.sh --livello rapido    # tiny (~80 MB), bilanciato=base, accurato=small
./verifica_test.sh --veloce
./diagnostica_sync.sh prj_xxxxxxxx
curl -s -X POST http://127.0.0.1:8020/api/projects/ID/timeline/rebuild \
     -H 'Content-Type: application/json' -d '{"mode":"asr","caption_source":"transcript"}'
```
