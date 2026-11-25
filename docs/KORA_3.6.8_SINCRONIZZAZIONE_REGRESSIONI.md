# KORA STUDIO LIGHT 3.6.8 — correzione della sincronizzazione (regressioni 3.6.7)

Aggiornamento **correttivo** della 3.6.7. Non ricostruisce l'applicazione, non cambia
l'interfaccia generale, non aggiunge provider: chiude i difetti trovati dall'audit
indipendente e li blocca con test di regressione.

---

## 1. Difetti corretti

### 1.1 La modalità «copione» non chiamava mai l'ASR

Prima: `rebuild_audio_timeline(mode="script", script_text=…)` costruiva i tempi
**stimandoli** dal testo, senza interrogare il motore di trascrizione. Inoltre
`apply_uploaded_voiceover()` faceva `use_asr = use_asr and not script_text`, quindi
un copione **spegneva** l'ASR.

Adesso (`kora_studio/agents/director.py`):

1. la modalità copione chiama `asr.transcribe()` sull'audio realmente importato;
2. confronta le parole riconosciute con il copione (`align_script_to_asr`);
3. dà a ogni parola del copione l'intervallo in cui è stata pronunciata
   (`timing_source="forced_aligned"`);
4. le parole del copione **non** riconosciute finiscono in `mismatches` e vengono
   mostrate nel pannello (e ripetute nel report QC);
5. il risultato è salvato nella timeline canonica con provenienza dichiarata;
6. senza motore ASR l'esito è `alignment="estimated"` con la nota
   «ASR non disponibile: tempi stimati dentro le pause misurate — nessun
   allineamento forzato»: una distribuzione proporzionale non viene più chiamata
   allineamento.

`apply_uploaded_voiceover()` ora passa `mode="script"` quando c'è un copione e
**non** disattiva più l'ASR.

### 1.2 Una scena può contenere più frasi

Prima: `caption_timings_from_transcript()` usava `next(...)`, quindi prendeva **solo
il primo segmento** della scena; la seconda frase veniva rimappata sui tempi della
prima. Con la scena 0–5 s, «Uno due» a 0,1–0,8 s e «tre quattro» a 2,2–3,0 s, la
parola «quattro» compariva a 0,4 s.

Adesso (`kora_studio/agents/audio_timeline.py`):

* raccoglie **tutti** i segmenti della scena, in ordine temporale;
* ogni parola conserva il tempo del proprio segmento (nessuna concentrazione);
* una frase che attraversa due scene viene divisa per parola sulla scena in cui è
  pronunciata (midpoint della parola dentro la finestra della scena);
* le scene senza parlato non producono sottotitoli e non inventano tempi;
* le pause restano quelle misurate (`silencedetect`), non distribuite a mano.

Test: `test_caption_includes_every_segment_of_the_scene` (scena 0–5 s → «quattro»
a 2,60 s), `test_silent_scene_and_pause_are_supported`,
`test_sentence_split_across_two_scenes_keeps_its_own_words`.

### 1.3 Lo stato «verificato» non è più un effetto collaterale dei timestamp

Prima: `build()` faceva `verified = bool(words) and words_have_timestamps`, quindi
anche parole con `timing_source="forced_aligned"` diventavano «trascrizione
verificata». `words_from_asr()` inoltre sovrascriveva la provenienza a `"asr"`.

Adesso:

* `words_from_asr()` **conserva** la provenienza dichiarata e aggiunge
  `recognized = (timing_source == "asr")`;
* `build()` calcola `alignment` dall'insieme delle fonti
  (`asr` / `forced_aligned` / `estimated` / `manual`);
* `verified = True` **solo** se `alignment == "asr"`, tutte le parole hanno
  `timing_source="asr"` e il motore ha fornito timestamp di parola;
* la timeline espone tre informazioni separate:

```json
"verification": {
  "timing_status": "forced_aligned",
  "text_status": "script_matched",     // oppure script_with_mismatches
  "verified": false,
  "words_from_asr": 0,
  "words_forced_aligned": 22,
  "words_estimated": 0
}
```

* il QC ha un nuovo controllo bloccante «Nessuna parola del copione spacciata per
  riconosciuta» (fallisce se `verified` è vero con parole non-ASR).

### 1.4 `verifica_test.sh --veloce` eseguiva la suite intera

Prima passava comunque `tests/kora_studio` a pytest. Adesso `--veloce` esegue
**solo**:

```
tests/kora_studio/test_v366_audio_timeline.py
tests/kora_studio/test_v367_speech_sync.py
tests/kora_studio/test_v368_sync_regression.py
```

### 1.5 La produzione non inventa più il testo che l'utente aveva chiesto all'ASR

Conseguenza diretta del punto 1: se in un progetto l'utente ha scelto «Trascrivi»
o «Allinea un copione» e il motore non c'è, la pipeline di produzione **non**
ripiega sul testo creativo dello storyboard. Usa il copione salvato
(`transcript_script_<project_id>`) o lascia la timeline senza parole, registrando
«Nessuna trascrizione disponibile: nessun testo inventato».

Nuove preferenze di progetto: `transcript_source_<id>` (`asr|auto|script|estimated`)
e `transcript_script_<id>`.

---

## 2. API invariata, risposta più esplicita

`POST /api/projects/{id}/timeline/rebuild` mantiene il contratto 3.6.7
(`mode`, `use_asr`, `script_text`, `asr_level`, `caption_source`; `mode` non valido → 400;
`mode="script"` senza copione → 400) e aggiunge nella risposta:

```json
{ "alignment": "forced_aligned", "verified": false,
  "verification": {...}, "mismatches": ["dicembre"] }
```

Nel pannello «Trascrizione & Sincronizzazione» le discordanze sono elencate con la
dicitura «Parole del copione non riconosciute nella registrazione (N)».

---

## 3. Test di regressione (nuovi)

`tests/kora_studio/test_v368_sync_regression.py` — 18 test, derivati dall'audit
indipendente e dalle richieste del committente:

| Area | Test |
|---|---|
| modalità copione | ASR chiamato davvero; allineamento forzato; discordanze dichiarate; senza ASR → stima dichiarata; copione mancante → errore; `apply_uploaded_voiceover` non spegne l'ASR |
| più frasi per scena | tutti i segmenti usati; «quattro» a 2,6 s; scena silenziosa; frase divisa su due scene |
| stato verified | copione allineato ≠ verificato; parole ASR = verificate; conteggi per provenienza |
| offset | 0 / 100 / 250 / 500 ms su parole, segmenti, cue e frame |
| HyperFrames | word highlight con i timestamp del transcript (0,1 / 0,4 / 2,2 / 2,6 s) |
| MP3 reali | import `voce_brando.mp3` e `voce_tiziana.mp3`, durata reale, `verified=false`, offset 0 |
| blocchi | la scena con `locks.caption` non viene riscritta |

Esecuzione:

```
./verifica_test.sh --veloce            # solo sincronizzazione (3 file)
.venv/bin/python -m pytest tests/kora_studio -q   # suite completa
```

---

## 4. Collaudo audiovisivo eseguito (dati reali, motore ASR assente)

Script riproducibile: `tools/accettazione_368.py` (artefatti in `docs/accettazione_3.6.8/`).

| Percorso | Audio | Modalità | Esito dichiarato | MP4 |
|---|---|---|---|---|
| A · MP3 + copione | `voce_brando.mp3` (21,08 s) | `script` | `alignment=estimated`, 22 parole stimate, 10 cue, 0 discordanze | 21,08 s · 527 frame · 1080×1920 · H.264 + AAC |
| B · MP3 senza copione | `voce_tiziana.mp3` (22,05 s) | `asr` | `alignment=none`, 0 parole, 0 cue (**nessun testo inventato**) | 22,05 s · 551 frame · 1080×1920 · H.264 + AAC |

Verifiche automatiche sul file prodotto: audio completo (durata MP4 ≥ fine voce − 0,2 s),
nessuna cue oltre la fine della voce, SRT esportato coerente con le cue della timeline,
frame estratti a 6 s e 18 s con sottotitoli bruciati e word-highlight HyperFrames.

**Perché non è il collaudo fonetico completo:** in questo ambiente `huggingface.co` è
irraggiungibile, quindi nessun modello Whisper può essere scaricato e **nessuna
trascrizione reale è stata eseguita**. I due percorsi sopra verificano la struttura
(import, timeline, scene, sottotitoli, render, export) con la provenienza dichiarata
`estimated` / `none`. Il collaudo fonetico (parole riconosciute e confrontate con
l'audio) va eseguito sul Mac dopo `./installa_asr.sh`:

```bash
cd ~/Documents/"Kora Studio Light"
./installa_asr.sh --verifica
./installa_asr.sh --livello bilanciato        # installa faster-whisper + modello, prova reale
./diagnostica_sync.sh                         # stato ASR e artefatti
```

Poi, nello Storyboard: **Ricalcola allineamento → Trascrivi automaticamente** (percorso B)
oppure **Allinea un copione** (percorso A). Con il motore installato la modalità copione
trascrive davvero, confronta, elenca le discordanze e salva
`alignment="forced_aligned"`, `verified=false`.

---

## 5. Non collaudato (dichiarato)

* trascrizione reale faster-whisper / Whisper di un MP3 italiano (modello non scaricabile qui);
* prova dei tre livelli di `installa_asr.sh` su macOS Intel e Apple Silicon;
* allineatore acustico dedicato (MFA/Gentle): l'allineamento resta testuale sui tempi ASR;
* diarizzazione e editor multi-traccia;
* click reali nel browser sui nuovi messaggi di discordanza in `SyncPanel`.
