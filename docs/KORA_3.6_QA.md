# KORA 3.6 — Rapporto di collaudo (QA)

Data: 22 novembre 2026 · build 3.6.0 · ambiente Linux (sandbox), FFmpeg 6.x via
binari del progetto.

## 1. Suite automatizzata

```text
.venv/bin/python -m pytest tests/kora_studio -q
209 passed, 3 skipped in 900,65 s (15:00)
```

- 178 test ereditati dalla 3.5 (tutti verdi);
- 19 nuovi test in `test_v36_model_manager.py` (errori provider, fail-fast, Model Manager);
- 12 nuovi test in `test_v36_consolidation.py` (stati QC, loudness, blocchi, manifest
  voce, ripristino, budget ideazione);
- 3 test saltati: richiedono provider o risorse esterne, dichiarati nel codice.

Classificazione: **unit + integration reali** su filesystem e FFmpeg reali;
**mock** solo per il CLI OpenCode assente; nessun test «dichiarato verde per
costruzione».

## 2. Controlli di installazione

```text
./verifica.sh
37 OK · 0 KO · 0 avvisi   (server fermo)
35 OK · 0 KO · 1 avviso   (server attivo: porta 8020 occupata, non è un guasto)
```

Nuovi controlli 3.6: Model Manager importabile con timeout 45 s di default,
quattro stati QC, `motion_speed` nei blocchi regia, `ripristina.sh` eseguibile,
master audio a −14 LUFS, versione 3.6.0 registrata.

## 3. Collaudo funzionale eseguito (reale)

| Prova | Esito | Dati |
|---|---|---|
| Avvio server e health | OK | `/api/health` risponde, payoff «The Art of Being» |
| Configurazione modelli via API | OK | ruoli e tempi corretti, persistenza verificata |
| Ricerca modelli senza CLI | OK (onesto) | `total: 0`, errore «opencode non trovato nel PATH» |
| Diagnostica modelli | OK | `installed: false`, nessun falso positivo |
| Progettazione Reel 20 s | OK | 8 scene, 500 frame |
| Produzione Reel 20 s | OK | 168,4 s, QC 17 superati + 1 non applicabile |
| Progettazione Reel 30 s | OK | 12 scene, 750 frame |
| Produzione Reel 30 s | OK | 253,4 s, QC 17 superati + 1 non applicabile |
| Anteprima completa 30 s | OK | 540×960, 25 fps, 25,2 s, 11 clip in cache |
| Anteprima singola scena | OK | 540×960, cache hit verificato |
| QC loudness reale | OK | −14,5 LUFS (20 s), −14,4 LUFS (30 s), TP entro −1 dBTP |
| Conteggio frame esatto | OK | 500/500 e 750/750 con `nb_read_frames` |
| Decodifica completa | OK | zero pacchetti corrotti |
| Logo e payoff in chiusura | OK | frame 29 s verificato a vista |
| Ripristino backup | OK | mappatura cartelle, percorsi ostili rifiutati, backup di sicurezza |
| Blocco regia su `motion_speed` | OK | rifiutato con motivazione |

## 4. Ispezione visiva

Frame estratti dal Reel 30 s (1 s, 8 s, 15 s, 22 s, 29 s):

- immagini autentiche del KORA (pergolato, tavoli, aperitivo al tramonto);
- sottotitoli leggibili nell'area sicura («Il pergolato dosa la luce invece di nasconderla.»);
- outro: logo originale KORA Beach Club su verde profondo, «The Art of Being» intatto,
  «POLICORO · MEDITERRANEAN SEA»;
- nessun frame nero anomalo (controllo `blackdetect` superato).

## 5. Prove non eseguite

Vedi `docs/KORA_3.6_FUNZIONI_NON_COLLAUDATE.md`: modelli OpenCode reali, TTS reale,
generazione C/D/E, vision. Nessuna di queste è presentata come funzionante.

## 6. Difetti trovati e corretti durante il collaudo

1. **Anteprima con clip video.** L'anteprima completa andava in errore quando una
   scena usava una clip video del Vault: il generatore di miniature apriva il file
   con Pillow come se fosse un'immagine (`UnidentifiedImageError`). Corretto
   estraendo il primo frame con FFmpeg. L'anteprima 30 s è stata rigenerata dopo
   la correzione.
2. **Fallback deterministico ingiustificato.** Se lo snapshot dei motori esisteva
   ma non conteneva modelli utilizzabili, KORA scendeva al progetto deterministico
   dichiarando «nessun motore attivo», che era falso. `fast_entry()` ora fa un probe
   reale (con cache) prima di arrendersi. Aggiunto test dedicato
   (`test_fast_entry_probes_when_snapshot_has_no_usable_model`).
3. **Test instabile (non un difetto del prodotto).** `test_api_bootstrap_and_project_reel`
   impostava lo snapshot **prima** del bootstrap; la prima scrittura delle
   impostazioni Essential azzera volutamente lo snapshot, quindi il test a volte
   partiva senza motore. Lo snapshot ora viene impostato dopo il bootstrap, come
   avviene nell'uso reale. La causa è documentata, non mascherata.

---

# Aggiornamento 3.6.1 — collaudo della correzione

## 7. Suite automatizzata 3.6.1

Nuovo file `tests/kora_studio/test_v361_ai_pipeline.py`: **11 test** (migrazione
legacy, preferenze conservate, scelta utente intoccata, Everyday→HY4 /
Premium→MiMo trasmessi esplicitamente, stesso modello fra Home e copione,
risposta valida accettata dopo 21 s, politica «Chiedi conferma» senza
deterministico, «Usa Nemotron», chiave Gemini non attiva, snapshot vecchio senza
deterministico, end-to-end con CLI doppio di prova).

Esito del file: **11 passed in 254,68 s**.

Suite completa (tutti i file):

```
.venv/bin/python -m pytest tests/kora_studio -q
```

Tre giri completi, tutti registrati:

| Giro | Esito | Durata | Note |
|---|---|---|---|
| 1 | `1 failed, 218 passed, 3 skipped` | 1283,85 s | fallisce `test_api_bootstrap_and_project_reel` (`calls == 0`): il difetto reale descritto al §8.2 |
| 2 | `1 failed, 219 passed, 3 skipped` | 977,76 s | fallisce `test_cold_first_request_does_not_scan_every_request` (`auth_calls 3 ≠ 1`): la discovery di avvio in background veniva contata come «probe per richiesta» |
| 3 | **`220 passed, 3 skipped`** | **977,27 s** | definitivo, nessun fallimento |

Correzioni nate dai giri:

* la seconda valutazione a sonda reale in `creative_entry` è stata limitata ai
  casi in cui lo snapshot **non conosce** il provider (provider collegato dopo
  l'ultima verifica). Se il provider è verificato e il modello non compare
  nell'elenco, non si torna a interrogarlo: si usa il percorso standard;
* il probe per provider è diventato *single-flight* (un lock per provider):
  due richieste ravvicinate non si autenticano due volte;
* il test `test_cold_first_request_does_not_scan_every_request` ora attende la
  discovery di avvio (che è una sola, in background) e misura che le richieste
  successive non interroghino il provider: è l'intenzione originale del test,
  espressa in modo misurabile.

I test aggiornati al comportamento corretto sono `test_v281_creative_fast_mode.py`
(politica esplicita, discovery di avvio attesa) e
`test_v31_engine_connect_and_variety.py` (Gemini non cambia il motore).

## 8. Difetti trovati e corretti nella 3.6.1

1. **Modello scelto non trasmesso.** Il piano automatico partiva dal modello
   legacy (DeepSeek) e ignorava il Model Manager. Ora provider e model ID sono
   passati esplicitamente (`creative_entry`).
2. **Deterministico ingiustificato con snapshot vecchio.** Se lo snapshot dei
   motori era fresco ma non conteneva il provider appena collegato,
   `creative_entry` non trovava il modello e ripiegava sul percorso generico,
   che poteva chiudere con zero chiamate. Ora c'è una seconda valutazione con
   sonda reale (`_model_entry_in_plan`) e un test dedicato.
3. **Timeout nascosto di 15 s.** Rimosso: il tentativo autorizzato riceve il
   budget di ideazione intero (45 s). Test: risposta accettata dopo 21 s.
4. **Gemini attivato dal solo collegamento della chiave.** Ora la chiave viene
   verificata e salvata, il motore non cambia.
5. **Temi locali restituiti senza consenso.** Con politica «Chiedi conferma» la
   risposta è la richiesta di decisione con quattro opzioni.
6. **Etichetta doppia del modello** (`opencode/opencode/…`): normalizzata in
   barra laterale, Home e log.

## 9. Prove non eseguite (dichiarate)

* chiamata reale a `opencode/hy4-flash-free`, `mimo-v2.6-flash-free`,
  `nemotron-3.5-lightning-free`: nell'ambiente di collaudo il CLI non esiste.
  La filiera è coperta dal doppio di prova e va confermata sul Mac con
  `./diagnostica_ai.sh`;
* misura dei tempi reali di ideazione sul Mac (benchmark dell'utente: 20,7 s /
  28,4 s / 78,4 s): usati come base per il budget di 45 s, non riverificati qui.
