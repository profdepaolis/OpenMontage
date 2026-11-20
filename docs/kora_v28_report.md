# KORA STUDIO 2.8 — Cinematic Intelligence & Production Stability

**Versione:** 2.8.0 · **Base:** 2.7.0 (stesso database, stessi provider, stessi archivi e produzioni precedenti)
**Branch:** `arena/36db4987-openmontage` · **Ambiente di verifica:** sandbox Linux senza browser headless, senza CLI OpenCode/Codex e senza chiavi API.

Questo report elenca **solo** ciò che è stato eseguito e verificato (test, render, log, interrogazioni ffprobe).
Dove una funzionalità non poteva essere verificata in questo ambiente, è dichiarato esplicitamente.

---

## 1. Selezione materiali: inventario, non sequenza

**Problema 2.7:** le foto scelte manualmente venivano assegnate ciclicamente (`selected_assets[idx % len]`), creando una sequenza forzata anche quando non aveva senso narrativo.

**2.8:**
- `KoraDirector.create_project` e `run_production_pipeline` non riassegnano più nulla a ciclo: gli asset selezionati diventano `authorized_asset_ids`.
- `CreativeDirector.build_storyboard(..., authorized_asset_ids=...)` limita **l'inventario** (anche la ricerca semantica, che prima poteva pescare fuori dalla selezione); il Cinematic Story Director sceglie la foto migliore per ogni scena in base a voiceover e obiettivo narrativo.
- `CreativeDirector.enforce_authorized_inventory()` ricolloca solo le scene fuori inventario (con motivazione scritta) e non tocca le scene già approvate.
- L'outro con logo è indipendente: `shot_source="logo_component"`, nessun asset dell'inventario autorizzato.

**Verifica:** `test_manual_selection_constrains_inventory_and_is_not_a_sequence` — il direttore riceve solo gli asset autorizzati, nessuna scena usa asset esterni, la sequenza **non** è `authorized[i % n]`, l'outro resta il logo.
Nel reel demo (10 foto autorizzate su 10) ogni scena usa un asset diverso, nessun ciclo.

## 2. Visual Intelligence: provenienza, 4 stati, aggiornamento automatico

Ogni analisi ora porta con sé: `sha256`, provider, modello, `prompt_version` (`vi-2.8.0`), data e stato.

| Stato | Significato |
|---|---|
| `verified` | un modello con visione **verificata** ha guardato i pixel |
| `deterministic` | analisi da statistiche reali dell'immagine, nessun modello |
| `stale` | prodotta da un fallback o da un modello non più quello configurato |
| `unavailable` | file dichiarato ma non leggibile |

- Le voci di cache senza provenienza (pre-2.8) sono considerate `stale` e ri-analizzate: nessun dato vecchio viene spacciato per valido.
- **Auto-upgrade:** quando un modello diventa vision-verified (`test_provider`/`vision_probe` con esito positivo) l'AI Engine Manager emette l'evento e `VisualIntelligence.upgrade_fallback_analyses()` rianalizza le voci `stale` — nessun intervento manuale.
- **Nessun tetto permanente alle prime N:** `annotate_vault(limit=None)` scorre **tutto** il Vault; `reanalyze_vault_all()` forza la rianalisi completa. API: `GET /api/vault/vision/status`, `POST /api/vault/vision/reanalyze`.
- Il pannello Media Vault mostra i conteggi per stato e il modello attivo, con il pulsante «Rianalizza tutto il Vault».

**Verifica:** `test_vision_provenance_states_upgrade_and_full_vault` — 13 foto (oltre il vecchio tetto di 12): partono tutte `deterministic`, con un motore visivo simulato diventano `stale` → `verified`, l'adapter riceve **davvero** gli allegati, e la seconda lettura è un cache hit.
Nel sandbox reale: `vault_status()` → 11 foto `deterministic`, 0 `stale`, 0 `unavailable`, nessuna firma visiva attiva (dichiarato).

## 3. Smart Shot Generator (`agents/smart_shot_generator.py`)

Nuovo agente che **legge i pixel** della fotografia reale e produce un'inquadratura derivata:

- salienza (gradiente + saturazione + peso centrale), orizzonte, texture, direzione prospettica dominante;
- finestra 9:16 migliore (ricerca multi-livello con immagine integrale), punto focale, **risoluzione residua** reale;
- grammatica di movimento per funzione narrativa (hook → push-in, contesto → pull-out, dettaglio → detail push, esperienza → parallasse, emozione → drift, chiusura → settle);
- 4 keyframe con easing, intensità, zoom start/end, `depth_simulation` (micro-parallasse dichiarata, **nessuna** ricostruzione 3D), motivazione narrativa;
- vincolo di autenticità: il crop deve stare **dentro** la foto e la larghezza visibile non scende sotto 540 px (upscale massimo 2×).

**Applicazione reale nel montaggio:** `AIMediaLab.animate_photo_deterministic(..., shot_plan=...)` ritaglia davvero la regione e costruisce l'espressione `zoompan` dai keyframe (`zoompan_expressions`), con le easing del piano. La chiave di cache delle clip include l'hash del piano: modificare l'inquadratura rigenera solo quella scena.

**Verifica:** `test_smart_shot_plan_stays_inside_the_photo` (crop dentro i bordi, residuo ≥540 px, keyframe ed easing validi, piano che esce dalla foto rifiutato) e `test_editor_really_consumes_the_shot_plan` (render 25 frame con piano vs. statico: i pixel cambiano → il piano è **applicato**, non descritto).
Nel reel demo 10 scene su 10 hanno `shot_plan` con `shot_source="smart_shot"`.

## 4. Story Director: piano completo e validato, riparazione dichiarata

`validate_plan()` applica i controlli richiesti:

1. **completezza** — ogni scena visiva ha esattamente un'inquadratura (mancanti/duplicati/sconosciuti segnalati);
2. **inventario valido** — asset esistente, autorizzato, visivo, non logo, con autenticità ammessa;
3. **compatibilità renderer** — framing/camera/transizione negli enum, durata > 0, nessun dettaglio su clip video;
4. **coerenza voiceover** — scena parlata ⇒ sottotitolo presente;
5. **diversificazione** — almeno 3 inquadrature distinte (quando ci sono ≥3 scene) e ≥2 movimenti con ≥4 scene; nessuna coppia identica consecutiva;
6. **continuità** — nessun asset ripetuto di fila (se ci sono meno asset che scene il riuso viene *dichiarato*, non nascosto);
7. **matematica dei frame** — totale coerente con fps.

Se il piano del modello non passa: **una sola** richiesta strutturata di correzione (`last_repair_request` con problemi e scene mancanti) e, se restano buchi, fallback deterministico **solo per quelle scene** (`source="fallback:missing_scene_N"`). Le scene valide non vengono riscritte.

**Verifica:** `test_story_director_repairs_only_missing_scenes` (3 scene, il modello ne restituisce 1 → il piano finale ha tutte e 3, le altre 2 dichiarate fallback, `complete=True`) e `test_story_director_rejects_unauthorized_and_unrenderable` (duplicati, camera inesistente, asset non autorizzato → piano non valido).
Il Story Director resta libero di scegliere ritmo, composizione, fuoco e movimento dal contenuto narrativo.

## 5. AI Engine Manager: tariffa ≠ stima ≠ costo reale

- `EngineResult` distingue `unit_price_per_1k_eur` (tariffa pubblicata), `estimated_cost_eur` (stima sui token), `cost_eur` (costo **reale**, `None` se non addebitato/noto) e `cost_basis` (`actual`, `estimated_from_unit_price`, `subscription`, `local_compute`, `free_quota`, `unknown`).
- Gli adapter HTTP non scrivono più la stima come costo reale; gli abbonamenti/locali verificati valgono 0 € con causale.
- `ai_activity` (migrazione 3) aggiunge: `estimated_cost_eur`, `unit_price_per_1k_eur`, `cost_basis`, `attachments`, `auth_type`, `request_status`, `vision_image`. Le chiamate **bloccate** (nessun modello visivo verificato) vengono registrate come `error` con `error_kind` esplicito: il monitor non tace i rifiuti.
- Budget extra resta **0 €** (`max_budget_eur = 0`), verificato nei test e nell'impostazione dell'installazione.

**AI Activity Monitor (UI):** provider/modello effettivi, agente richiedente, auth, allegati trasmessi, fallback, tariffa/1k, costo stimato, costo reale, stato della richiesta.
**Verifica:** `test_unit_price_estimate_and_actual_cost_are_distinct` (tariffa 0,5 €/1k, stima > 0, reale `None`, riepilogo `actual=0`, `estimated>0`), `test_subscription_zero_and_extra_budget` (0 € reali, budget 0), `test_activity_monitor_shows_auth_attachments_and_vision`.

## 6. Cinematic Preview e modifiche conversazionali per scena

- `CinematicPreviewRenderer` produce un MP4 **540×960** con le stesse clip derivate (piano di inquadratura reale) + sottotitoli, e un manifest con **una card per scena**: foto originale, inquadratura derivata (crop, zoom, easing, pixel reali), movimento camera, voiceover/sottotitoli, durata, transizione, sound design; miniatura «originale» e «inquadratura».
- **Cache:** manifest e clip per hash dello storyboard + audio; cambiare una scena rigenera solo quella clip (le altre sono riusate — `scene_clips_cached`).
- **Modifica conversazionale di una scena:** `SingleSceneReviser` (motore se disponibile, altrimenti parser deterministico dichiarato) limita i campi a inquadratura, movimento/intensità, testo, sfx, transizione, zoom; la scena viene marcata `modificata`, le altre restano identiche e quelle **approvate** sono elencate come preservate. Approvazione per scena con `POST /scenes/{n}/approve`.
- API: `POST /api/projects/{id}/preview`, `GET …/preview`, `GET …/preview/video`, `GET …/preview/scene/{n}/thumb/{source|derived}`, `POST …/scenes/{n}/revise`.

**Verifica:** `test_preview_manifest_has_all_card_elements_and_cache` (elementi richiesti, frame totali, cache hit al secondo giro, ffprobe 540×960 con frame esatti), `test_single_scene_revision_keeps_other_scenes` (scena 2 cambia, 1/3/4 no, approvate preservate, clip riusate), `test_preview_api_flow_and_vision_endpoints` (intero flusso via API).
Anteprima del reel demo: 750 frame, 540×960, 11 card, 51 s di calcolo.

## 7. Flusso UX

`Suggerisci tema` → `Progetta Reel` → **anteprima cinematografica** → `Realizza Reel`.
La creazione progetto non lancia più il render automatico (`auto_start=false`): si apre la sezione Preview con player, card e modifiche per scena; il pulsante «Realizza Reel (1080×1920)» avvia la produzione completa.
Le proposte creative continuano a usare le **miniature reali del Vault**; i parametri cinematografici non sono richiesti all'utente (li decidono gli agenti), le impostazioni avanzate restano raggiungibili.

## 8. Test

| Tier | Cosa copre | Come |
|---|---|---|
| **Simulated** | costi, routing, riparazione piano, eventi vision | adapter finti (`source="simulated"`), nessuna rete |
| **Integration** | selezione asset, cache vision, smart shot + render, preview, API, frame math, QC | DB temporaneo, Vault reale, FFmpeg reale |
| **Real provider** | OpenCode/Codex reali | **saltato** con motivo dichiarato (nessuna CLI/account in questo ambiente) |

- Nuovo file: `tests/kora_studio/test_v28_cinematic_intelligence.py` (15 test: 14 passati, 1 saltato).
- **Comando unico e riproducibile:** `./run_kora_tests.sh` (equivalente a `python -m pytest tests/kora_studio -q`).
- Esito completo dell'ultima esecuzione su questa 2.8 (`./run_kora_tests.sh`): **78 passed, 3 skipped in 490,55 s** — 3 skip dichiarati (2 real-provider 2.7 già presenti + 1 nuovo real-provider 2.8).
- **Il pacchetto ZIP 2.8 include integralmente `tests/`** (nella 2.7 l'archivio conteneva solo `kora_studio/`): l'archivio viene creato con pathspec espliciti `kora_studio tests docs run_kora_tests.sh`.

## 9. Produzione demo obbligatoria (30 s)

| Voce | Valore verificato |
|---|---|
| Tema | generato dal Creative Director: «Brand identity KORA: The Art of Being, il mattino che resta sul mare» |
| Obiettivo | Brand Identity |
| Progetto | `prj_7f1584d4cd` — «Brand identity KORA: The Art of Be — Luce del Mattino» |
| Motore AI | **OpenCode non installato** in questo ambiente e nessuna chiave configurata ⇒ tutti gli agenti hanno operato in fallback deterministico dichiarato (nessuna chiamata finta, nessun costo inventato) |
| Foto | 10 fotografie reali KORA del Media Vault (tutte autorizzate) |
| Voiceover Brando | **non inserito**: l'archivio Brando è legato al proprio copione e non esiste un motore TTS autorizzato/configurato in questo ambiente (dichiarato dal Voice Director: `richiede_sintesi_o_registrazione`) |
| Formato | 1080×1920 · 25 fps · H.264/AAC · 750 frame **esatti** (`nb_read_frames=750`) · 30.000 s |
| Struttura | 10 scene derivate + outro logo |
| Outro | logo originale KORA + payoff «The Art of Being» + «POLICORO · MEDITERRANEAN SEA» |
| QC | **superato** — 17/17 controlli (durata, risoluzione, fps, codec, audio, clipping, decodifica completa, **conteggio esatto dei frame**, neri anomali, integrità asset/logo, autenticità, continuità narrativa, coerenza voiceover/sottotitoli, payoff) |
| Anteprima | 540×960, 750 frame, 11 card, cache riusata |

**File consegnati**
- `kora_data/exports/demo/kora_demo_v28_brand_identity_30s.mp4` (12,5 MB)
- `kora_data/exports/demo/v28/kora_demo_v28_preview_540p.mp4` (anteprima)
- `kora_data/exports/demo/v28/preview_manifest.json` (card per scena)
- `kora_data/exports/demo/v28/qc_report.json` e `prj_7f1584d4cd_report_tecnico.{md,json}`
- `kora_data/exports/demo/v28/ai_activity_project.json` e `ai_activity_all.json`
- `kora_data/exports/demo/v28/prj_7f1584d4cd_progetto_modificabile.json`

**Log AI della produzione:** le chiamate registrate risultano `error` con `error_kind="nessun_motore"` (nessun provider disponibile), costo reale 0 € e costo stimato 0 €: nessun importo inventato. Le richieste visive bloccate vengono registrate come `visione_non_verificata`.

## 10. Limiti dichiarati (nessuna funzionalità dichiarata sulla sola presenza del codice)

- **Interfaccia:** verificata con `tsc --noEmit` e `next build` (export statico) e con test API reali; **non** con un browser headless (non disponibile nella sandbox).
- **OpenCode/Codex:** non installati né autenticati qui ⇒ i percorsi reali restano coperti dal test saltato con motivo; il resto è coperto da adapter simulati e dal fallback deterministico realmente eseguito.
- **Vision:** nessun modello con visione verificata in questa installazione ⇒ tutte le analisi sono `deterministic`, e lo dichiarano.
- **Voiceover Brando:** disponibile solo per il copione d'archivio; per un nuovo testo servono ElevenLabs/OpenAI/Piper configurati.

## 11. Aggiornamento (comando unico)

Dopo aver scaricato lo ZIP 2.8 dalla cartella Drive condivisa, dalla cartella dell'installazione:

```bash
cd ~/Desktop/KORA-STUDIO && curl -L --fail --retry 3 -o /tmp/kora.zip "<LINK_ZIP_2.8>" \
 && unzip -tq /tmp/kora.zip >/dev/null \
 && cp -R kora_studio "/tmp/kora_studio_backup_$(date +%Y%m%d-%H%M)" \
 && unzip -oq /tmp/kora.zip -d . && rm -f /tmp/kora.zip ; pkill -f "kora_studio serve" ; sleep 1 ; (nohup ./start.sh > /tmp/kora_server.log 2>&1 &) ; sleep 15 ; open http://127.0.0.1:8000
```

Test, dalla stessa cartella: `./run_kora_tests.sh`.
