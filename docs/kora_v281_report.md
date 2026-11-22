# KORA STUDIO 2.8.1 — Creative Director · Performance Optimization

**Versione:** 2.8.1 · **Base:** 2.8.0 (stesso database, stessi provider, stessi archivi e produzioni)
**Obiettivo:** rendere «Suggerisci un tema» veloce, prevedibile e annullabile, senza introdurre nuove funzionalità prima di aver risolto la lentezza.

---

## 1. Sintesi

Il pulsante **Suggerisci un tema** non passa più dal percorso pesante:

* il contesto inviato al modello è **compatto** (profilo brand come sintesi filosofica, stagionalità, temi già usati, categorie di materiale, richiesta utente) invece del profilo completo + inventario fotografico;
* l'AI Engine Manager **non esegue più una scansione provider/modelli per richiesta**: usa uno snapshot persistente (installati, autenticazione, modelli, capacità, tipo di fatturazione, ultimo controllo) aggiornato in background all'avvio e su richiesta;
* esiste un **budget globale di 25 s** (configurabile) con **primo tentativo ≤ 15 s**, **un solo fallback AI autorizzato** e **fallback deterministico** alla scadenza;
* l'annullamento è **reale**: evento di cancellazione propagato fino al processo CLI, che viene terminato;
* la UI mostra **stage reali** del backend, tempo trascorso, motore usato e un pulsante **Annulla**;
* le proposte sono **4 e leggere** (titolo, hook, messaggio centrale, direzione narrativa, obiettivo/voce/durata consigliati, max 3 asset reali). Voiceover, storyboard, prompt generativi, Vision e specifiche di montaggio arrivano **solo dopo** la scelta del tema;
* le proposte AI vengono messe in **cache** (chiave: versione profilo KORA, stagione, storico creativo, configurazione utente, provider+modello, data) e le proposte in cache sono **etichettate**.

Nessuna regressione: la vecchia ideazione completa (`suggest_four_themes`, `/api/creative/suggest`, `/api/creative/from-theme`) resta disponibile e invariata nel comportamento.

---

## 2. Flusso Fast Creative Mode

```
POST /api/creative/suggest-lite          → 202 immediata { run_id, budget_ms, stages }
GET  /api/creative/suggest-lite/{id}     → stato + eventi reali + envelope finale
GET  /api/creative/suggest-lite/{id}/stream → SSE (alternativa al polling)
POST /api/creative/suggest-lite/{id}/cancel → annullamento reale
POST /api/creative/expand-theme          → espande la proposta scelta nel concept completo
POST /api/creative/suggest-lite/prewarm  → pre-generazione facoltativa (solo se attiva e a costo zero)
```

Stage emessi dal backend, con timestamp misurati:

| Stage | Cosa misura |
|---|---|
| `context` | costruzione contesto compatto (profilo, stagione, storico, categorie, ID utilizzabili) |
| `connect` | selezione motore dallo snapshot (nessuna discovery per richiesta) |
| `generate` | inferenza reale, con eventuale `fallback` verso un secondo motore autorizzato |
| `validate` | parsing JSON e validazione schema/duplicati/temi già usati/ID asset |
| `done` | envelope finale (o `fallback` deterministico / `cancelled`) |

### Budget e timeout

* `global_budget_sec` = **25 s** (8–60 configurabile)
* `first_attempt_sec` = **15 s** (3–60 configurabile)
* un solo fallback AI, **solo se autorizzato** dalle impostazioni del motore e **solo entro il tempo residuo**
* alla scadenza: **fallback deterministico locale (0 €)** con 4 proposte complete e motivo dichiarato
* il timeout copre discovery, connessione, avvio CLI, inferenza, parsing e fallback; il processo CLI viene ucciso (`proc.kill()` in `run_cli`)

### Cache

Tabella `theme_proposal_cache` (migrazione 4):

```
cache_key (PK sha256) · payload_json · brand_version · season · provider · model
created_at · expires_at · hits · last_hit_at
```

* TTL predefinito 6 h, non oltre la giornata (la data è nella chiave)
* le proposte servite dalla cache hanno `from_cache = true`, `source = "cache"` e `cache_age_sec`
* un tema già usato non viene mai ri-proposto: i titoli presenti nello storico vengono filtrati; se restano meno di 4 proposte valide si usa il fallback deterministico
* pre-generazione in background: **disattivata per default** (`background_warm=false`); se attivata parte solo con un motore a costo zero verificato

---

## 3. Modifiche tecniche

| Area | File | Modifica |
|---|---|---|
| Schema lite | `kora_studio/db/models.py` | `ThemeProposalLite`, `ThemeProposalsLiteEnvelope` |
| Migrazione 4 | `kora_studio/db/database.py` | `ai_activity.timings_json`; tabella `theme_proposal_cache` + indice scadenza |
| Fast mode | `kora_studio/agents/theme_ideation.py` | nuovo servizio: run asincroni, stage reali, budget, cancel, cache, parsing validato, fallback deterministico, prewarm |
| Direttore creativo | `kora_studio/agents/creative_director.py` | `suggest_four_themes_lite`, `expand_lite_proposal`, estrazione `_deterministic_concepts` riusata dal percorso classico |
| AI Engine Manager | `kora_studio/ai_engine/manager.py` | snapshot provider/modelli (`refresh_snapshot`, `ensure_snapshot`, `start_snapshot_refresher`, `fast_plan`), `deadline`/`attempt_timeout`/`max_candidates`/`brand_context` in `call`, preferenza modello veloce, fascia `fast_creative` nelle impostazioni |
| Adapter / risultato | `kora_studio/ai_engine/base.py` | `EngineResult.call_id` e `EngineResult.timings` |
| AI Activity | `kora_studio/ai_engine/activity.py` | `timings_json` su ogni chiamata + `annotate_timings` per fasi tardive (parsing/validazione) |
| API | `kora_studio/api/app.py` | endpoint lite (start, stato, cancel, SSE, cache stats/clear, expand, prewarm), snapshot refresher all'avvio, versione 2.8.1 |
| Frontend API | `kora_studio/frontend/src/lib/api.ts`, `…/lib/types.ts` | `suggestThemesLite`, `getSuggestThemesLite`, `cancelSuggestThemesLite`, `expandTheme`, tipi lite/run |
| UI Home | `kora_studio/frontend/src/components/HomeHub.tsx` | timeline stage, tempo/budget, motore, badge cache, Annulla, blocco doppie richieste, 4 card lite, espansione al click, messaggi d'errore espliciti |
| Monitor | `kora_studio/frontend/src/components/AIEngineView.tsx` | pannello Fast Creative Mode (budget, primo tentativo, modello veloce) + colonna «Tempi misurati» con fasi distinte |
| Test | `tests/kora_studio/test_v281_creative_fast_mode.py` | 19 scenari |
| Versione | `kora_studio/__init__.py`, `app.py` | 2.8.1 |

### Nessun cambio di modello senza autorizzazione

Il campo «modello veloce preferito» è **solo una preferenza**: viene applicato se quel modello esiste già tra quelli autorizzati del provider scelto; la configurazione `providers.*.model` dell'utente non viene mai riscritta (verificato dal test 18).

---

## 4. Misure

### 4.1 Percorso reale in questo ambiente (nessun motore AI installato)

Comando di riferimento: avvio server `python -m kora_studio serve --port 8012`, 5 richieste consecutive, `deterministic_offline_fallback`.

| Metrica | 2.8.0 (`/api/creative/suggest`) | 2.8.1 (`/api/creative/suggest-lite`) |
|---|---|---|
| Prima risposta HTTP | attesa fino a fine elaborazione (bloccante) | **21–37 ms** (ritorno immediato con `run_id`) |
| Tempo totale fino a 4 proposte | 52–57 ms (solo perché non c'è AI) | **100–153 ms** (include polling e 4 proposte deterministiche) |
| Tempi interni backend | non misurati per fase | **58–73 ms** totali, con fasi separate |

### 4.2 Percorso AI simulato (adapter finti, nessuna rete)

Scenario operativo principale, 12 richieste consecutive:

```
n=12  min=81.4 ms  median=81.7 ms  max=102.9 ms  successo=100%
```

Altri scenari misurati dai test:

| Scenario | Risultato |
|---|---|
| Timeout del modello (delay 30 s, primo tentativo 3 s) | chiuso in **< 6 s** con fallback deterministico |
| Primo motore bloccato + secondo motore veloce, budget 8 s | completato in **< 7,5 s**, motore usato = secondo provider, `fallback=1` |
| Annullamento durante l'inferenza | stato `cancelled` in **< 4 s**, evento di cancel ricevuto dall'adapter, nessuna richiesta registrata rimasta |
| JSON non valido | fallback deterministico, 4 proposte, stage `validate:error` |
| Provider non autenticato | 4 proposte deterministiche con motivo esplicito |
| Cache (stessa richiesta due volte) | seconda esecuzione `from_cache=true`, **nessuna nuova inferenza** |

### 4.3 Cosa non è misurabile qui (dichiarato)

* **OpenCode/Codex reali**: in questo ambiente non sono installati e non ci sono chiavi API. La lentezza originale (probe per richiesta + timeout 120 s per candidato) è stata rimossa nel codice e sostituita da snapshot + budget, ma il guadagno su un'installazione con OpenCode **non è misurato qui**: va verificato nella tua installazione.
* **Verifica «attraverso il browser»**: non è possibile in questo ambiente (nessun browser installato: né Chromium, né Firefox, né Playwright). La UI è stata verificata con `tsc --noEmit`, `next build`, ispezione dei bundle generati (`out/_next/static/chunks/…` contiene le stringhe «Fast Creative Mode», «Scegli questo tema», «dalla cache», «Tempi misurati») e con test HTTP reali sugli endpoint.
* **Server/SDK persistente di OpenCode**: non valutabile senza il binario installato. La CLI resta l'implementazione attuale, con **avvio del processo misurato** (`run_cli` registra `spawn_ms` → `cli_startup_ms` nell'Activity Monitor) e processo terminato alla scadenza.
* **Nessun accesso a file/tool per l'agente creativo**: la configurazione scritta nella workspace del CLI nega `edit`, `bash`, `webfetch`, `write`, `patch` e `external_directory`; l'ideazione lite invia solo il prompt compatto (`brand_context=False`) e nessun allegato.

---

## 5. Test

`tests/kora_studio/test_v281_creative_fast_mode.py` — 20 test, **20 passati** (≈46 s):

1. OpenCode autenticato → 4 proposte lite, nessun allegato, prompt compatto
2. Prima richiesta a freddo senza scansione per richiesta
3. Scenario operativo principale, 12 esecuzioni con min/mediana/max e successo 100%
4. Modello non disponibile → fallback dichiarato
5. Provider non autenticato → messaggio esplicito
6. Nessuna rete / quota esaurita → fallback dichiarato
7. Timeout del modello → primo tentativo chiuso al budget
8. JSON non valido → fallback deterministico
9. Fallback deterministico → sempre 4 proposte, nessun campo pesante
10. Annullamento durante l'elaborazione (anche lato motore)
11. Budget globale + un solo fallback autorizzato
12. Cache etichettata e riusata
13. Temi già usati mai ri-proposti
14. API: start, polling, cancel, 404
15. AI Activity Monitor con fasi distinte
16. Espansione della proposta scelta in concept completo (solo dopo la scelta)
17. Nessuna regressione sull'ideazione classica
18. Il modello scelto dall'utente non viene mai cambiato
19. Pre-generazione in background solo se attivata e a costo zero
20. Fallback deterministico visibile nel Monitor AI con le sue fasi reali

Suite completa (`./run_kora_tests.sh`): **98 passed, 3 skipped in 523,06 s** (101 test raccolti: tutti i test 2.7 e 2.8 più i 20 nuovi; nessuna regressione su progettazione e rendering dei Reel)

---

## 6. Come aggiornare (comando unico)

```bash
curl -L --fail --retry 3 -o /tmp/kora.zip "https://drive.usercontent.google.com/download?id=1xJktUuQaxu-lukDVQzgcBqFTr118L5np&export=download&confirm=t" \
  && unzip -tq /tmp/kora.zip \
  && cp -R kora_studio "/tmp/kora_studio_backup_$(date +%Y%m%d-%H%M)" \
  && unzip -oq /tmp/kora.zip -d . && rm -f /tmp/kora.zip \
  ; pkill -f "kora_studio serve" ; sleep 1 \
  ; (nohup ./start.sh > /tmp/kora_server.log 2>&1 &) ; sleep 15 ; open http://127.0.0.1:8000
```

La migrazione 4 viene applicata automaticamente al primo avvio (nuova colonna `timings_json`, nuova tabella `theme_proposal_cache`). Nessun dato esistente viene toccato.

---

## 7. Limiti dichiarati

* Le misure «prima/dopo» in questo ambiente riguardano il percorso deterministico e adapter simulati: con un provider reale i tempi dipendono dal provider stesso, ma sono ora **limitati dal budget** e **sempre terminabili**.
* Le proposte in cache possono essere più vecchie della stagione corrente fino a 6 h (o alla mezzanotte): sono sempre etichettate.
* La pre-generazione in background è spenta per default e, anche se attivata, rifiuta motori non a costo zero.
* Il monitor mostra i tempi misurati solo per le chiamate successive alla 2.8.1 (le righe storiche hanno `timings_ms` vuoto).
