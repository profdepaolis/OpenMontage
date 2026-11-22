# KORA Studio Light 3.6.1 — ripristino della pipeline AI

Documento di consegna della correzione 3.6.1. Nessuna funzione nuova, nessun cambio
di design: solo il ripristino di «Suggerisci un tema → Progetta Reel → Realizza Reel»
con il motore creativo configurato.

---

## 1. Diagnosi: perché la 3.6 tornava a DeepSeek e alle proposte locali

| # | Causa accertata | Effetto visibile |
|---|---|---|
| 1 | `ThemeIdeationService._resolve_entry()` usava `engine.fast_entry(IDEATION_AGENT)`: il piano automatico partiva dalla **configurazione legacy** (`providers.opencode.model` / `agents.creative_director.model` = `nvidia/deepseek-ai/deepseek-v4.1-flash`) | il riquadro laterale mostrava `opencode · deepseek-v4.1-flash`; il modello scelto nel Model Manager (HY4) non veniva mai chiamato |
| 2 | `_first_attempt_sec()` conservava il default nascosto di **15 s** | HY4 (20,7 s misurati) veniva interrotto a 15 s: timeout, poi proposte `kora-deterministic` |
| 3 | Con politica «Chiedi conferma», ogni fallimento finiva in `_finish_deterministic()` | quattro temi locali restituiti come se fossero la risposta dell'AI, senza consenso |
| 4 | `connect_gemini_api()` impostava `essential.active_provider = gemini_api` | collegare la chiave Google spegneva OpenCode; la Home mostrava Gemini attivo con «costo sconosciuto: autorizzazione specifica non concessa» |
| 5 | `ModelManager.config()` e `_selected_role_model()` leggevano la chiave grezza, che in 3.6 poteva essere `{}` | i default HY4/MiMo/Nemotron non venivano applicati e il legacy restava l'unica fonte |
| 6 | `candidate_plan()` ignorava un modello esplicito assente dall'elenco del CLI | anche indicando HY4 a mano, il piano ripiegava sul modello del CLI |

Il timeout a 45 s percepito nell'interfaccia era il *wait* del polling del frontend:
il backend chiudeva il tentativo a 15 s e rispondeva subito con il deterministico.

---

## 2. Correzioni applicate

### 2.1 Model Manager come fonte unica (requisito 1)

`kora_studio/ai_engine/model_manager.py`

* `migrate_legacy(engine)`: legge `providers.opencode.model` e
  `agents.creative_director.model`; se valgono uno dei tre modelli misurati li
  conserva, se sono dismessi (DeepSeek v4.1) propone **HY4 Flash** e scrive la
  sostituzione in `migration.detail`; archivia i valori originali in
  `ai_model_legacy_archive` e **svuota le chiavi legacy**, che non possono più
  scegliere il modello;
* una scelta esplicita dell'utente (`user_choice: true`, scritta da qualsiasi
  salvataggio nella schermata Modelli) non viene **mai** sovrascritta;
* `effective_choice()` restituisce provider, modello, ruolo (everyday/premium),
  politica e timeout;
* `record_inference()` registra l'ultima inferenza reale (esito, latenza, errore);
* `status()` alimenta la diagnostica della Home e di `/api/ai-models/status`.

La migrazione gira all'avvio in `KoraDirector.__init__` ed è idempotente.
Endpoint manuale: `POST /api/ai-models/migrate-legacy`.

### 2.2 Selezione esplicita del modello (requisito 2)

`kora_studio/ai_engine/manager.py`

* `creative_entry(agent, purpose)`: chiede il modello al Model Manager e lo passa
  al motore con `provider_override` / `model_override`. Per i CLI reali la scelta
  vince anche su un elenco modelli vecchio (`_entry_for_model` sintetizza la voce
  con la fatturazione dichiarata dal nome `-free`);
* il piano automatico non può più scegliere un modello diverso da quello
  configurato; i modelli `-free` sono trattati come quota gratuita (zero costo)
  anche quando il CLI non dichiara il piano — il costo sconosciuto continua a
  bloccare gli altri provider;
* `theme_ideation` e `reel_designer` usano entrambi `creative_entry`, quindi il
  modello del copione è lo stesso mostrato nella Home.

### 2.3 Nessun limite nascosto (requisito 3)

`kora_studio/agents/theme_ideation.py`

* l'unico tentativo autorizzato riceve il **timeout di ideazione** (45 s di
  default) meno ~1,3 s di overhead reale (parsing + validazione);
* se la politica è «Passa da solo al modello alternativo» viene riservata una
  parte del budget al secondo tentativo (max 45 % del totale);
* test dedicato: una risposta valida dopo **21 s** viene accettata e produce 4
  proposte AI (prima veniva scartata).

### 2.4 Gemini non cambia il motore (requisito 4)

`connect_gemini_api()` salva e verifica la chiave, ma **non** tocca `essential`:
OpenCode resta il motore creativo, Gemini resta un'estensione inattiva finché
l'utente non la attiva. La risposta include `activated: false`.

### 2.5 Fallback con consenso (requisito 5)

Con politica «Chiedi conferma» un fallimento **non** produce più temi locali:

* lo stato del run è `needs_decision`, con il motivo reale (`timeout di ideazione
  a 45 s: …`, `motore dismesso`, `CLI non installata`, …);
* la richiesta resta in memoria e il frontend mostra quattro scelte esplicite:
  **Riprova**, **Usa Nemotron**, **Usa proposte locali**, **Annulla**
  (`POST /api/creative/suggest-lite/{run_id}/decide`);
* «Usa proposte locali» è l'unico percorso che genera il deterministico, e la
  risposta resta marcata `deterministic_offline_fallback` / `source:
  deterministic`;
* le altre politiche restano disponibili: «locale» (deterministico subito),
  «fallback» (Nemotron automatico, locale solo se anche quello non risponde),
  «stop» (errore esplicito).

### 2.6 Diagnostica visibile (requisito 7)

Home: provider e modello reali, modalità Everyday/Premium, ultimo test superato
con latenza, ultimo esito di inferenza, politica di fallback, eventuale nota di
migrazione. Barra laterale: provider · modalità e modello attivo. La schermata
Impostazioni › Modelli mostra la nota di migrazione.

---

## 3. File modificati

| File | Modifica |
|---|---|
| `kora_studio/ai_engine/model_manager.py` | migrazione legacy, `effective_choice`, `status`, `record_inference` |
| `kora_studio/ai_engine/manager.py` | `creative_entry`, voce sintetica per modello esplicito, inferenza registrata, Gemini non attiva |
| `kora_studio/ai_engine/adapters_cli.py` | modelli `-free` = quota gratuita (zero costo) |
| `kora_studio/agents/theme_ideation.py` | modello esplicito, timeout pieno, `needs_decision`, `decide()`, log |
| `kora_studio/agents/reel_designer.py` | stesso modello della Home, timeout di design dal Model Manager |
| `kora_studio/agents/director.py` | migrazione all'avvio, collegamento Model Manager ⇄ motore |
| `kora_studio/api/app.py` | versione 3.6.1, `ai_status` nel bootstrap, `/api/ai-models/status`, `/api/ai-models/migrate-legacy`, `/decide` |
| `kora_studio/frontend/src/components/HomeHub.tsx` | barra diagnostica, pannello delle quattro decisioni |
| `kora_studio/frontend/src/app/page.tsx` | barra laterale con modalità e modello reali |
| `kora_studio/frontend/src/components/CreativeEngineModels.tsx` | nota di migrazione |
| `kora_studio/frontend/src/lib/{api,types}.ts` | endpoint e tipi delle decisioni/status |
| `setup.sh`, `verifica.sh` | versione 3.6.1 (37 controlli, incluso lo script diagnostico) |
| `diagnostica_ai.sh` | **nuovo** script diagnostico per macOS |
| `tests/kora_studio/test_v361_ai_pipeline.py` | **nuovo** — 11 test di regressione |
| `tests/kora_studio/test_v281_creative_fast_mode.py`, `test_v31_engine_connect_and_variety.py` | test aggiornati al comportamento corretto (politica esplicita, Gemini non attivo) |

Nessun asset, progetto, database o configurazione viene cancellato: la 3.6.1
lavora sul DB esistente e la migrazione archivia i valori legacy prima di
sostituirli.

---

## 4. Test di regressione

`tests/kora_studio/test_v361_ai_pipeline.py` (11 test):

1. migrazione DeepSeek → HY4 con nota e chiavi legacy svuotate (idempotente);
2. una preferenza legacy valida (MiMo) viene conservata;
3. una scelta esplicita dell'utente non viene mai sovrascritta;
4. «Suggerisci un tema» usa il modello del Model Manager: Everyday → HY4,
   Premium → MiMo (verificato sul modello realmente passato all'adapter);
5. «Progetta Reel» usa lo stesso modello della Home;
6. risposta valida dopo **21 s** accettata, con timeout del tentativo ≈ 44 s;
7. politica «Chiedi conferma»: `needs_decision`, nessuna proposta locale, quattro
   opzioni; «Usa proposte locali» genera il deterministico dichiarato;
8. «Usa Nemotron» esegue il modello alternativo;
9. la chiave Gemini non cambia il motore attivo;
10. uno snapshot vecchio (senza il provider appena collegato) non deve
    trasformare un motore funzionante in proposte locali;
11. **end-to-end**: bootstrap → suggest (4 proposte `ai_engine`) → expand →
    project-reel (1 chiamata, 10 scene) → asset associati → produce sync → job
    nella pipeline → MP4 **1080×1920, 25 fps, 20,00 s** → QC.

Il CLI reale non è disponibile nell'ambiente di collaudo: i test 4-11 usano un
eseguibile `opencode` **doppio di prova** che percorre lo stesso codice di
produzione (adapter CLI, subprocess, timeout, JSON, Model Manager, rendering).
Nessun test dichiara «HY4 funziona» per finta: la verifica sul modello reale va
eseguita sul Mac con `./diagnostica_ai.sh` (§6).

---

## 5. Risultati delle prove eseguite

### 5.1 Suite completa

```
.venv/bin/python -m pytest tests/kora_studio -q
```

Esito definitivo: **220 passed, 3 skipped in 977,27 s** (tre giri documentati in
`docs/KORA_3.6_QA.md` §7). I giri completi hanno fatto emergere due difetti
reali, entrambi corretti:

* con uno snapshot dei motori vecchio, `creative_entry` non trovava il modello
  configurato e poteva ripiegare sul deterministico: aggiunta una seconda
  valutazione a sonda reale, **solo** quando il provider non è ancora noto allo
  snapshot (`_model_entry_in_plan`, test 10);
* il probe per provider non era *single-flight*: due richieste ravvicinate
  potevano autenticarsi due volte. Ora c'è un lock per provider.

### 5.2 Prova end-to-end sull'applicazione avviata (CLI doppio di prova)

Server reale su porta 8022, dati in `/tmp/kora_demo361`, CLI `opencode` simulato:

| Passo | Esito |
|---|---|
| bootstrap | `opencode/hy4-flash-free`, modalità Everyday, politica «Chiedi conferma» |
| Suggerisci un tema | `done` in 310,7 ms, `ai_engine:opencode/hy4-flash-free`, 4 proposte `source: ai_engine` |
| espansione tema | 1 concept |
| Progetta Reel | 1 sola chiamata, 10 scene, 500 frame |
| fotografie | 10/10 scene con asset autentici del Vault |
| Realizza Reel | job presente in `/api/jobs` |
| MP4 | **1080×1920, 25/1 fps, 20,000 s**, 9,8 MB |
| QC | superato, 18 controlli (1 non applicabile: voiceover assente, dichiarato) |
| durata totale | 285,8 s (rendering compresso nella sandbox) |

Artefatto: `/tmp/KORA_361_e2e_cli_simulato.mp4` (prova tecnica, non un Reel di
produzione: il modello era simulato).

### 5.3 Difetti corretti durante il collaudo

* `creative_entry` restituiva un altro provider quando il modello configurato non
  era nell'elenco: ora accetta solo la coppia provider+modello richiesta;
* un adapter registrato poteva risultare «nessun motore» per uno snapshot non
  fresco: aggiunta la rete di sicurezza `fast_entry` → `resolve` con probe reale
  (eliminata la flakiness di `test_api_bootstrap_and_project_reel`);
* etichetta doppia `opencode/opencode/hy4-flash-free`: normalizzata;
* snapshot dei motori non aggiornato (provider collegato dopo l'ultima
  verifica) → `creative_entry` ripiegava sul percorso generico e poteva
  arrivare al deterministico: aggiunta la seconda valutazione con sonda reale.

### 5.4 Prova del pacchetto 3.6.1 in cartella pulita

| Passo | Esito |
|---|---|
| estrazione dello ZIP | 201 voci, cartella `Kora Studio Light/` |
| `setup.sh` con `HOME` vuoto | 7/7 passi, FFmpeg/FFprobe scaricati in locale, interfaccia già compilata |
| `verifica.sh` | **36 OK · 0 KO · 1 avviso** (porta 8020 occupata dal server di sviluppo) |
| avvio su porta 8023 | `/api/health` ok, UI `200`, `ai-models/status` = `opencode/hy4-flash-free` |
| `diagnostica_ai.sh` | legge lo stato, esegue i due test reali e dichiara «CLI opencode non trovata nel PATH» (2 OK · 3 KO): nessun falso positivo |
| aggiornamento da 3.6.0 | «Installazione precedente rilevata: 3.6.0 → aggiornamento a 3.6.1», copia di sicurezza `pre_aggiornamento_…db` creata |

---

## 6. Diagnostica sul Mac (script)

```bash
cd "Kora Studio Light"
./diagnostica_ai.sh            # stato + test rapido + test creativo reale
KORA_PORT=8023 ./diagnostica_ai.sh
./diagnostica_ai.sh --fix      # riesegue la migrazione della config legacy
```

Lo script legge `/api/ai-models/status` (provider, modello, modalità, politica,
ultima inferenza, migrazione), esegue un test rapido e un **test creativo reale**
(4 temi, latenza misurata) sul modello Everyday, e stampa le ultime chiamate del
Monitor AI. Esce con codice 0 solo se il motore creativo è operativo.

Verifica manuale equivalente:

```bash
opencode --version                  # il CLI risponde?
opencode auth list                  # account collegato?
open "http://127.0.0.1:8020"        # Home: Motore / Modalità / Ultimo test
```

---

## 7. Aggiornamento sicuro

1. **Backup prima di tutto**: dalla Home, Impostazioni › Backup › «Crea backup»
   (oppure `curl -X POST http://127.0.0.1:8020/api/backup/create`).
2. Chiudi KORA (`Ctrl+C` nella finestra o chiudi «Avvia KORA.command»).
3. Sostituisci la cartella `Kora Studio Light` con quella della 3.6.1 **senza
   toccare** `kora_data/` (vault, progetti, database, voiceover, loghi).
4. Doppio clic su `Installa KORA.command`: rileva la 3.6 e mostra
   «installazione precedente 3.6.0 → aggiornamento a 3.6.1», senza reinstallare i
   modelli né cancellare dati. In caso di dubbio `verifica.sh` dice cosa manca.
5. `./diagnostica_ai.sh`: se il modello risulta ancora DeepSeek, `--fix` applica
   la migrazione (equivalente a riavviare l'app).
6. Apri la Home: deve comparire `opencode · hy4-flash-free`, modalità Everyday.
7. Premi «Suggerisci un tema»: entro 45 s arrivano quattro proposte generate
   (badge verde `ai_engine:opencode/hy4-flash-free`). Se il motore non risponde,
   compare il pannello con le quattro scelte — non quattro temi locali.

Ripristino di emergenza: `./ripristina.sh` (usa `KORA_PORT` se il server è su
un'altra porta).
