# KORA STUDIO 3.0 — Essential (refactoring strategico)

**Base:** 2.8.1 (stesso database schema v4, stesso archivio media, stesso profilo KØRA, stesse voci autorizzate, stesso logo)
**Obiettivo:** reel cinematografici eccellenti, con una sola richiesta creativa e nessuna complessità di provider visibile.

---

## 1. Cosa è cambiato (e cosa NON è stato toccato)

| Ambito | Prima (2.8.1) | Essential |
|---|---|---|
| Motore creativo | discovery multi-provider, più agenti AI per reel | **un solo motore attivo** (`opencode` di default quando autenticato), estensioni disattivate non vengono né caricate né sondate |
| Progetta Reel | ideazione + vision + story director + regia AI separate | **una sola richiesta** (`ReelDesigner.design`), JSON con concept, storyboard, sound design, copertina, caption |
| Regia | direzione potenzialmente ciclica (goals/motions a rotazione) | arco narrativo derivato dal tema; la matematica dei frame resta deterministica |
| Navigazione | più sezioni, refresh globale | **Studio — Archivio — Progetti — Impostazioni**, caricamento per singola sezione |
| Polling | refresh periodico di tutto | solo job attivo, 1,5 s, nessun polling sovrapposto |
| Diagnostica provider | nella Home | solo in *Impostazioni → Motore AI / Provider & budget* |

Componenti **non riscritti** perché funzionanti: motore FFmpeg, HyperFrames, Smart Shot Generator, Quality Controller deterministico, Brand Guardian, Media Vault, DB e migrazioni, Export Manager, voci autorizzate.

## 2. Una richiesta creativa, una sola chiamata

`kora_studio/agents/reel_designer.py`

```python
design = director.design_reel(request, proposal=None)   # 1 chiamata engine
project = director.create_project_from_design(design, theme)
director.run_production_pipeline(project["id"])         # nessuna chiamata LLM ordinaria
```

* `engine.call(AGENT="creative_director", json_mode=True, max_candidates=1, brand_context=False)`
* `annotate_vault=False` e `use_story_director=False`: la seconda chiamata AI che il test intercettava (Cinematic Story Director dentro `build_storyboard`) **non avviene più**.
* Qualunque errore (motore assente, `EngineError`, JSON non valido, schema non rispettato) → progetto deterministico KORA con `fallback_reason` dichiarato e riga 0 € nel monitor AI. **Nessuna catena di provider.**
* Validazione: ≥3 scene, frame esatti (`durata_s × 25`), outro KORA.
* Fallback deterministico **derivato dal tema**: tre direzioni (identità / ospitalità / stagione) con hook, righe di voiceover e ordine degli asset autentici diversi. Prima della correzione due temi diversi producevano MP4 **byte-identici**; ora non accade (vedi §4).

## 3. Misure reali sul campo (percorso deterministico, dichiarato)

Ambiente: sandbox Linux senza `opencode`/`codex`, senza chiavi API, senza modello vocale Piper installato.

| Produzione | Design | Creazione progetto | Render | Frame | QC |
|---|---|---|---|---|---|
| Brand Identity (20 s) | 0,97 s | 0,01 s | 144,0 s | 500/500 | 17/17 |
| Experience & Hospitality (30 s) | 0,41 s | 0,01 s | 217,0 s | 750/750 | 17/17 |
| Seasonal (30 s) | 0,05 s | 0,01 s | 217,3 s | 750/750 | 17/17 |

QC deterministico (tutti superati): 1080×1920, 25 fps, durata esatta, H.264/AAC, nessun clipping, decodifica completa, conteggio frame, nessun nero anomalo, integrità asset e logo originale, payoff "The Art of Being", sottotitoli sincronizzati, voiceover/sottotitoli coerenti, nessun riferimento promozionale scaduto.

**Queste cifre non sono benchmark AI**: il motore creativo reale non è installato in questo ambiente. La latenza con OpenCode è misurata dal campo `timings_ms` di `ReelDesign` quando il motore risponde.

## 4. Consegna verificabile

Cartella `kora_data/exports/essential/`:

```
kora_essential_brand_identity.mp4          8.35 MB  1080x1920  25 fps  20.000 s  500 frame
kora_essential_experience_hospitality.mp4 14.70 MB  1080x1920  25 fps  30.000 s  750 frame
kora_essential_seasonal.mp4               14.30 MB  1080x1920  25 fps  30.000 s  750 frame
kora_essential_*_cover.png                copertine originali KORA (titoli diversi per tema)
kora_essential_*_caption.txt              caption Instagram
verifica.json                             hash, ffprobe, QC per ciascun reel
```

I tre file hanno hash SHA-256 diversi (anche Experience e Seasonal, che prima coincidevano).

## 5. Limiti dichiarati (nessun dato simulato)

* **Nessun voiceover nei tre MP4**: in questo ambiente non esiste alcun motore TTS utilizzabile (nessun modello Piper `.onnx`, nessuna chiave ElevenLabs/OpenAI, `edge-tts` richiede rete). L'audio contiene musica CC0 + SFX; i sottotitoli riportano il copione. La pipeline ha dichiarato `richiede_sintesi_o_registrazione` invece di riusare una vecchia registrazione con testo diverso.
* **Motore creativo**: `deterministic_offline_fallback` (0 €). Il percorso a una sola chiamata è provato dai test con un adapter simulato, non da OpenCode reale.
* **UI**: verificata con `tsc`/build; non esiste un browser in sandbox, quindi nessuna verifica visiva interattiva.

## 6. Step 10 — segmenti vocali riusati

`VoiceDirector.prepare_voiceover` mantiene una cache per segmento in `kora_data/cache/voice_segments/<sha256(voce|testo)>.wav`:
se testo e voce non cambiano, il segmento non viene risintetizzato anche quando la timeline cambia; i tempi si riallineano sul parlato reale (`sync_scenes_to_speech_pacing`). Con un TTS configurato questo evita chiamate ripetute e costi doppi in caso di revisione della durata.

## 7. Come attivare il motore reale

1. `Impostazioni → Motore AI`: selezionare l'unico provider attivo (`OpenCode`).
2. Verificare che lo stato sia `disponibile` (l'estensione non attiva non viene sondata: nessun rumore, nessuna attesa).
3. `Studio → Progetta Reel`: la stessa azione usa una sola richiesta; i tempi compaiono in `preferences.reel_design.timings_ms`.
4. Per la voce: ElevenLabs (chiave + Voice ID) oppure modello Piper locale in `kora_data/voices/piper`.

## 8. Test

* `tests/kora_studio/test_v30_essential.py` — 10 test (una sola chiamata, frame esatti, fallback dichiarato, bootstrap, project-reel).
* Esito reale (21/11/2026): `pytest tests/kora_studio` → **108 passed, 3 skipped in 551,90 s**; `next build` → compilazione e TypeScript OK.
* Isolamento test corretto: i default di `AIEngineManager` contenevano liste mutabili condivise (`enabled_providers`) che venivano modificate in-place fra test; ora `settings()` usa `deepcopy`. Inoltre `conftest` isola anche `db.database.DB_PATH` e `voice_director.CACHE_DIR`.
