# KORA STUDIO 2.7 — AI Engine Reliability · rapporto di verifica

Data: 20 novembre 2026 · versione `2.7.0`

## Cosa è cambiato rispetto alla 2.6

| Requisito | Prima (2.6) | Ora (2.7) |
|---|---|---|
| 1. Adattatori CLI | `local=True`, `subscription=True`, `cost_eur=0.0` per tutti i modelli | metadata per singolo modello: provider reale, id, tipo di autenticazione, fatturazione, piano verificabile, esecuzione locale/remota, costo noto/sconosciuto, quota. Il costo 0 € esiste solo se `auth list` espone il piano o il calcolo è locale |
| 2. Multimodalità | immagini scartate dal `_flatten()` | allegati inviati con i parametri della CLI installata (OpenCode `--file`, Codex `--image` quando supportato) o inline base64 per le API; un motore che non trasferisce immagini **rifiuta** la richiesta invece di svuotarla |
| 2. Visione verificata | dedotta dal nome del modello | `vision_probe` invia un'immagine a contenuto noto (numero 47, sfondo verde acqua, triangolo rosso) e promuove il modello solo se la risposta cita il contenuto |
| 3. Centralizzazione | Creative Director con percorsi diretti Ollama/Gemini/OpenAI/Claude/DeepSeek | unica porta: `AIEngineManager`; i percorsi diretti sono stati rimossi. Tutti e 9 gli agenti ricevono il manager |
| 3. Servizi media separati | mescolati ai motori di testo | `MEDIA_SERVICES` (voce, immagine, video) elencati separatamente; nessun modello di testo produce media |
| 4. Routing | fallback implicito | provider/modello espliciti rispettati; fallback solo `off` / `free_only` / `authorized`; `local_only` esclude qualsiasi inferenza remota, anche via CLI locale; gestiti timeout, rate limit, quota esaurita, modello non disponibile, annullamento |
| 4. Controllo economico | budget per agente | `_authorize` centralizzato prima di ogni chiamata: costo sconosciuto ⇒ bloccato senza autorizzazione specifica, anche con budget disponibile |
| 5. UI | stato e bottoni parziali | per provider: installazione, autenticazione, modello attivo, fatturazione, capacità testo/immagini, ultimo test, tempo di risposta, disponibilità reale; bottoni Rileva, Test connessione, Test testo, Test foto, Prova visione, Aggiorna modelli, Imposta primario, Registro chiamate; cambio motore senza riavvio |
| 6. AI Activity Monitor | assente | tabella `ai_activity`: produzione, agente, provider e modello reali, inizio, durata, esito, tentativi, fallback, costo reale/stimato/sconosciuto. Nessun prompt, nessuna chiave |
| 7. Cinematic Intelligence | asset con `idx % len(goals)`, fasi cicliche | `CinematicStoryDirector` distribuisce l'arco narrativo in proporzione alle scene (hook → payoff), valida framing/movimenti/transizioni/SFX sui valori ammessi e sugli asset reali; `VisualIntelligence` analizza le foto con un modello visivo verificato e fa cache |
| 8. Quality Control | stima `round(durata × 25)` se il conteggio falliva | `nb_read_frames` obbligatorio: se non è eseguibile il QC **fallisce**. Confronto esatto: 20 s = 500 frame, 30 s = 750 frame @ 25 fps |
| 9. Test | 44 | 64 passed, 2 skipped (OpenCode/Codex non installati in questo ambiente: test reali dichiarati non eseguibili, mai simulati) |

## Test eseguiti

```
$ ./.venv/bin/python -m pytest tests/kora_studio -q
64 passed, 2 skipped in 387.49s (0:06:27)
```

* `test_ai_engine_manager.py` — 21 test: adattatori CLI, metadata onesti, budget 0, costo sconosciuto, visione verificata, fallback dichiarato, `local_only`, API.
* `test_ai_engine_v27.py` — 15 test + 2 skip: story director non ciclico, piano validato, visual intelligence con cache, copywriter con Brand Guardian, activity monitor senza prompt, conteggio esatto dei frame, nessuna chiamata diretta ai motori di testo, 9 agenti collegati.
* `test_pipeline_and_api.py`, `test_upgrade_2_acceptance.py`, `test_brand_and_vault.py`, `test_providers_and_cost.py` — pipeline completa, export, QC, brand, costi.

Saltati per assenza nell'ambiente (motivo dichiarato dal test): `test_real_opencode_account_reports_verifiable_plan`, `test_real_codex_account_is_detected`.

## Reel realmente renderizzato

* Produzione `prj_d7bba42e6d` — «Il mattino che resta», 20 s, 1080×1920, 25 fps.
* QC: **passed**, fotogrammi **500 / 500 esatti** (conteggio `nb_read_frames`, nessuna stima).
* 8 scene con arco narrativo proporzionale (hook → contesto → dettaglio → esperienza → emozione → payoff → outro logo), movimenti e transizioni distinti, asset reali del Vault, logo originale in chiusura.
* File: `kora_data/exports/demo/kora_demo_v27_il_mattino_che_resta_20s.mp4`.
* Motore AI: nessun account configurato in questo ambiente ⇒ fallback deterministico dichiarato (`deterministic_offline_fallback`), costo 0 €; il registro ha annotato i tre tentativi falliti con costo «sconosciuto».

## Come leggere lo stato (nessuna finzione)

* `operativo` = una generazione reale è riuscita.
* `autenticato` = credenziali/sessione presenti, ma nessuna generazione verificata.
* `installato` = binario o endpoint presente, autenticazione assente.
* `indisponibile` = non utilizzabile (con motivo).
* Costo: mostrato solo se pubblicato o provato (piano CLI / quota / locale); altrimenti «sconosciuto» e la chiamata viene bloccata quando il budget è 0 € o l'autorizzazione ai costi ignoti è spenta.
