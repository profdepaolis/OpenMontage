# KORA STUDIO LIGHT 3.6 — Report di consolidamento

Versione: **3.6.0** · data: 22 novembre 2026
Base: KORA Studio Light 3.5 (178 test verdi, 31 controlli di installazione)
Natura dell'intervento: **consolidamento, non riscrittura**. Nessun motore nuovo,
nessun agente permanente aggiunto, nessun renderer sostituito.

Payoff KORA, logo originale, gerarchia di autenticità e identità visiva restano
immutati.

---

## 1. Causa radicale risolta all'origine

**Sintomo riferito:** con OpenCode la generazione falliva in circa 1,2 s, ma KORA
mostrava un timeout dopo 25 s.

**Causa reale:** il modello `nvidia/deepseek-ai/deepseek-v4.1-flash` è stato
dismesso dal provider il 16 novembre 2026. Il CLI esce quasi subito e **scrive la
causa nel flusso di risposta**; il codice guardava solo l'exit code e il timeout
globale, quindi aspettava inutilmente fino alla scadenza.

**Correzione (in `kora_studio/ai_engine/base.py` e `adapters_cli.py`):**

- nuova funzione `provider_failure()` che riconosce, anche con exit code 0,
  le famiglie: modello dismesso/EOL, rete, risposta vuota, JSON non valido;
- nuova classe `EngineModelRetired` (sottoclasse di `EngineModelUnavailable`, quindi
  instradata come «motore non operativo» e sostituibile dal candidato successivo);
- l'errore viene sollevato **subito**, con il testo originale del provider, la
  richiesta conservata (`retry_payload`) e i tempi (`timings`);
- i timeout veri restano timeout veri: nessun allargamento dei tempi per nascondere
  un errore di configurazione.

Verificato da 19 test dedicati (`tests/kora_studio/test_v36_model_manager.py`).

---

## 2. Model Manager (nuovo, leggero)

`kora_studio/ai_engine/model_manager.py` + endpoint `/api/ai-models/*` + pannello
«Modelli & benchmark» in Impostazioni.

| Requisito | Stato | Note |
|---|---|---|
| Elenco modelli con `opencode models` (senza argomento provider) | implementato | riusa `OpenCodeAdapter.raw_model_ids()` |
| Cache dell'elenco (30 min) e aggiornamento manuale | implementato | preferenza `ai_opencode_models_cache` |
| Ricerca e filtro per provider | implementato | `/api/ai-models/search?q=&provider=` |
| Scelta modello principale / premium / alternativo | implementato | preferenza `ai_model_config` |
| Verifica autenticazione e versione CLI | implementato | mostrate nel pannello e in diagnostica |
| Test di inferenza reale (rapido) | implementato | prompt «Rispondi soltanto con OK» |
| Test creativo (4 temi KORA in JSON) | implementato | verifica strutturale, non solo presenza di testo |
| Ultimo test + latenza per modello | implementato | storico ultime 120 prove |
| Nessun benchmark automatico o periodico | rispettato | solo azione esplicita dell'utente |
| `-free` ≠ gratuità verificata | rispettato | il pannello lo dichiara; costo resta «non verificabile» |

Valori di default: `opencode/hy4-flash-free` (Everyday), `opencode/mimo-v2.6-flash-free`
(Cinematic Premium), `opencode/nemotron-3.5-lightning-free` (alternativo).
Tempi massimi modificabili: ideazione 45 s, piano completo 90 s, revisione mirata 45 s,
premium 150 s. Il timeout dell'ideazione è ora **letto dalla configurazione** sia nel
servizio di ideazione sia nel client, e viene mostrato in UI con il valore reale.

Politica di fallback: chiedi (default) / automatico autorizzato / locale / fermati.
Il fallback non è mai presentato come risposta dell'IA.

---

## 3. Esperienza in tre passi

Home: **01 Suggerisci un tema → 02 Progetta Reel → 03 Realizza Reel**, con gli
ingressi alternativi (idea libera, media personali, ripresa) spostati su una riga
secondaria. Il passaggio 02/03 porta alla sezione di progettazione; se non esiste
ancora, avvia l'ideazione invece di mostrare un vicolo cieco.

Messaggi d'errore riscritti in italiano semplice: modello dismesso, quota,
autenticazione, rete, JSON non valido, tempo scaduto (con il valore reale), con
azioni «Riprova» e indicazione di dove cambiare modello o alzare il tempo.

---

## 4. Qualità: quattro stati, misure reali

`QualityCheckItem` ora espone `status` ∈ {`superato`, `non_superato`,
`non_applicabile`, `non_verificato`} con etichetta italiana; `QualityReport`
conserva `passed` (retrocompatibile) e aggiunge `qc_status_counts`.

Regole applicate:

- export senza audio → «Audio AAC», «Nessun clipping», «Loudness» = **Non applicabile**;
- Reel senza voiceover → sincronizzazione sottotitoli = **Non applicabile**;
- conteggio frame non eseguibile → **Non verificato** (non un falso superamento);
- analisi volumedetect non eseguibile → **Non verificato**;
- solo i controlli «Non superato» gravi bloccano la consegna.

**Master audio:** il mux finale applica `loudnorm=I=-14:TP=-1.5:LRA=11`. Il QC misura
davvero loudness integrata e true peak con FFmpeg e li riporta. Sul Reel 30 s di
questa build: **−14,4 LUFS**, true peak entro −1 dBTP, 750/750 frame.

Il report tecnico esportato riporta ora i quattro stati e il conteggio per stato.

---

## 5. Audio: manifest e cache per segmento

Ogni segmento vocale è identificato dall'hash SHA-256 di un manifest completo:
testo + hash del testo, voce, provider, modello, parametri (rate, pitch, stile,
ID voce provider, studio), lingua, versione del copione. Il manifest viene salvato
accanto al WAV in cache.

Conseguenze verificate:

- cambiare solo la timeline **non** rigenera la voce;
- cambiare testo, voce, provider o parametri invalida solo i segmenti interessati;
- il risultato della generazione espone `segment_manifest` con hash, cache hit e
  provider reale di ogni scena.

Nessuna sostituzione silenziosa di voce: Edge TTS resta dichiarato come fallback
e non viene mai etichettato Brando/Tiziana.

---

## 6. Blocchi (lock) e protezioni

Il blocco «regia artistica» ora include anche `motion_speed` e `zoom_level`:
modificare la velocità del movimento a scena bloccata viene rifiutato dal backend
con motivazione parlante. Restano attivi i blocchi scena, voce, durata, sottotitoli,
visual. Outro con logo originale e payoff resta protetto.

---

## 7. Backup e ripristino reversibile

- `BackupManager.restore_backup()` mappa esplicitamente le cartelle
  (`originals/` → `vault/originals/`), rifiuta percorsi non sicuri (`..`, assoluti);
- prima del ripristino crea un **backup di sicurezza** dello stato attuale;
- nuovo `ripristina.sh` (elenca, ripristina l'ultimo o un file specifico, chiede
  conferma, si rifiuta di lavorare con KORA attivo sulla porta 8020);
- nuovo endpoint `POST /api/backup/restore` con `confirm=true` obbligatorio e
  vincolo che il file sia dentro la cartella backup KORA.

---

## 8. Provenienza delle generazioni di scena

Il registro A–E ora contiene: provider, modello, prompt e suo hash, riferimenti,
parametri (durata, verticale, varianti, crop), data, durata reale dell'operazione,
costo e classe di costo, validazione (file esistente, esito dichiarato dal motore,
revisione umana richiesta) e `provenance_version`.

Le modalità C/D/E restano dichiarate **non collaudate** finché un provider non
restituisce un file reale: nessuna di esse viene presentata come funzionante.

---

## 9. Cosa non è stato collaudato (dichiarazione esplicita)

L'ambiente di questa build **non ha** il CLI `opencode` né accesso HTTP esterno.
Pertanto:

| Area | Stato | Motivo |
|---|---|---|
| `opencode models` reale | **non collaudato** | CLI assente nel sandbox |
| Switch tra modelli reali | **non collaudato** | CLI assente |
| Test rapido/creativo su provider | **non collaudato** | CLI assente |
| Errore EOL reale dal provider | **collaudato solo su testo** | firma del messaggio riprodotta in test; nessuna chiamata reale |
| Generazione A/B locali | collaudato in suite | elaborazione locale, nessun provider |
| Generazione C/D/E | **non collaudato** | richiede provider e autorizzazione di spesa |
| TTS reale (ElevenLabs/OpenAI/Piper/Edge) | **non collaudato** | nessuna credenziale/voce disponibile qui |
| Rendering 20 s e 30 s | **collaudato** | FFmpeg locale, QC con misure reali |
| Ripristino backup | collaudato | test con archivio costruito a mano |

Dettaglio completo in `docs/KORA_3.6_FUNZIONI_NON_COLLAUDATE.md`.

---

## 10. File modificati e aggiunti

Nuovi:
- `kora_studio/ai_engine/model_manager.py`
- `kora_studio/frontend/src/components/CreativeEngineModels.tsx`
- `ripristina.sh`
- `tests/kora_studio/test_v36_model_manager.py` (19 test)
- `tests/kora_studio/test_v36_consolidation.py` (12 test)
- documenti `docs/KORA_3.6_*.md`, questo report

Modificati (principali):
- `ai_engine/base.py` — errori 3.6, `provider_failure`, classificazione EOL/rete
- `ai_engine/adapters_cli.py` — fail-fast su errore nel flusso, risposta vuota tipizzata
- `ai_engine/manager.py` — modello scelto nel Model Manager come preferenza
- `agents/director.py` — istanza `model_manager`
- `agents/creative_director.py` — timeout ideazione da configurazione
- `agents/theme_ideation.py` — budget ideazione = timeout configurato (45 s default)
- `agents/quality_controller.py` — quattro stati, loudness reale, non applicabili
- `db/models.py` — `status` QC e helper
- `agents/cinematic_editor.py` — master −14 LUFS / −1,5 dBTP
- `agents/voice_director.py` — manifest e chiave cache per segmento
- `agents/scene_editor.py` — `motion_speed`/`zoom_level` nel blocco regia
- `agents/export_manager.py` — report QC con stati
- `agents/scene_generator.py` — provenienza completa
- `backup/backup_manager.py` — ripristino mappato e reversibile
- `api/app.py` — endpoint modelli, backup restore, versione 3.6.0
- `frontend/src/app/page.tsx`, `HomeHub.tsx`, `lib/api.ts` — tre passi, errori, pannello modelli
- `verifica.sh` (37 controlli), `INSTALLAZIONE.md`, `LEGGIMI.txt`, `tools/pack_kora.py`

Dipendenze: **nessuna aggiunta**, nessuna rimozione.

---

## 11. Prestazioni misurate in questa build

| Operazione | Tempo |
|---|---|
| Suite pytest completa (209 test + 3 skip) | 900,7 s |
| `verifica.sh` (37 controlli) | ~5 s |
| Progettazione deterministica 20 s (8 scene) | < 1 s |
| Produzione completa Reel 20 s (500 frame) | 168,4 s |
| Progettazione deterministica 30 s (12 scene) | < 1 s |
| Produzione completa Reel 30 s (750 frame) | 253,4 s |
| Anteprima completa 540×960 del Reel 30 s | 25,2 s (11 clip in cache) |
| Anteprima scena 540×960 | ~2 s (cache attiva) |
| `/api/ai-models/config`, `/search`, `/diagnostics` | < 1 s |

Misure reali sul Reel 30 s: 1080×1920, 25 fps, 30,000 s, **750/750 frame**,
H.264 yuv420p, AAC 48 kHz stereo, QC **17 superati + 1 non applicabile**, **−14,4 LUFS**. Sul Reel
20 s: 500/500 frame, 20,000 s, QC **17 superati + 1 non applicabile**.

Tempi dei modelli AI: **non misurati** in questa build (CLI assente). I valori di
riferimento del prompt (3,0 s Everyday / 20,7 s creativo, 15,8 / 78,4 s Premium)
non sono stati riprodotti qui e non vengono inventati.

---

## 11b. Installer 3.6 e collaudo in ambiente pulito

- `KORA_Studio_Light_3.6_installer.zip` — 59 807 395 byte, 172 voci, cartella
  superiore `Kora Studio Light/`;
- contiene 142 file: applicazione, interfaccia compilata, asset KORA originali,
  demo 20 s e 30 s (con copertine, didascalie e report), `ripristina.sh`,
  `KORA_3.6_REPORT.md`, documentazione;
- **nessun** `.venv`, `kora_data`, `node_modules`, `__pycache__` o chiave;
- doppio clic: `Installa KORA.command` (setup + verifica + avvio), poi
  `Avvia KORA.command`;
- prova in cartella pulita con `HOME` vuoto: `setup.sh` completato,
  `verifica.sh` → **35 OK · 0 KO · 1 avviso** (porta occupata dal server di
  sviluppo), app raggiungibile, `/api/ai-models/config` e `/diagnostics` corretti;
- prova di aggiornamento: marcatore `3.5.0` presente → «Installazione precedente
  rilevata: 3.5.0 → aggiornamento a 3.6.0», copia di sicurezza del database
  creata, nessun dato toccato;
- prova di ripristino nella stessa cartella: backup creato via API, `ripristina.sh
  --ultimo --si` → 15 file ripristinati, backup di sicurezza generato, app
  riavviata con Brand Brain e payoff intatti.

## 12. Difetti corretti durante il collaudo

1. anteprima in errore con clip video nel Vault (miniatura letta come immagine);
2. fallback deterministico ingiustificato quando lo snapshot dei motori era
   presente ma senza modelli: ora c'è un probe reale prima di arrendersi;
3. test `test_api_bootstrap_and_project_reel` instabile: impostava lo snapshot
   prima del bootstrap, che azzera volutamente lo snapshot. Corretto il test, non
   il comportamento del prodotto.

## 13. Problemi residui dichiarati

1. OpenCode non installato nell'ambiente di collaudo → tutta la filiera modelli è
   verificata solo per struttura e test simulati sul testo del provider.
2. La percezione «timeout di 25 s» è corretta alla radice, ma senza CLI reale non
   è possibile misurare il tempo di fallimento su EOL.
3. Il QC loudness usa `loudnorm` in una passata: il valore misurato può oscillare
   di qualche decimo di LUFS rispetto a una misura a due passate.
4. Le modalità C–E del generatore restano non collaudate per assenza di provider
   e autorizzazione di spesa.

Nessuna funzione è stata marcata come «funzionante» senza una prova reale.

---

## 14. Aggiornamento 3.6.1 — ripristino della pipeline AI

Dettaglio completo: `docs/KORA_3.6.1_RIPRISTINO_AI.md`.

**Cause trovate (sei, tutte nel prodotto):** la scelta del modello era ancora
delegata al piano automatico alimentato dalla configurazione legacy (DeepSeek
v4.1); il primo tentativo aveva un limite nascosto di 15 s; con la politica
«Chiedi conferma» ogni fallimento restituiva temi locali senza consenso;
collegare Gemini cambiava il motore attivo; la configurazione del Model Manager
letta in forma grezza non applicava i default; un modello esplicito assente
dall'elenco del CLI veniva ignorato.

**Correzioni:** migrazione automatica della configurazione legacy (DeepSeek →
HY4, preferenze valide conservate, scelte esplicite mai sovrascritte, chiavi
legacy archiviate e svuotate); `creative_entry()` che trasmette provider e model
ID del Model Manager; timeout di ideazione intero (45 s) al tentativo
autorizzato; politica «Chiedi conferma» con quattro scelte esplicite
(Riprova / Usa Nemotron / Usa proposte locali / Annulla) e nessun deterministico
automatico; Gemini resta estensione inattiva; diagnostica visibile in Home
(provider, modello, modalità, ultimo test, ultima inferenza, fallback,
migrazione).

**Prove:** 11 nuovi test (`test_v361_ai_pipeline.py`) inclusi migrazione,
Everyday→HY4 / Premium→MiMo, risposta valida accettata dopo 21 s, decisioni,
Gemini e un end-to-end completo con CLI doppio di prova che produce un MP4
1080×1920 · 25 fps · 20,00 s con QC superato. Suite completa: **220 passed,
3 skipped in 977,27 s**. Prova sull'applicazione avviata:
suggest in 310,7 ms con 4 proposte `ai_engine:opencode/hy4-flash-free`, design in
una sola chiamata, 10 scene, 10 fotografie autentiche, job visibile in pipeline,
QC 18 controlli (1 non applicabile), 285,8 s totali.

**Cosa resta da fare sulla macchina dell'utente:** eseguire `./diagnostica_ai.sh`
con OpenCode reale. Nell'ambiente di collaudo il CLI non esiste: nessuna misura
di HY4/MiMo/Nemotron è dichiarata come reale in questo report.
