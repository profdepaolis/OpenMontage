# KORA STUDIO LIGHT 3.6.6 — Sincronizzazione audio-first

Data: 2026-11-23 · Branch `arena/36db4987-openmontage`

## Il difetto che questa versione chiude

Nella 3.6.5 ogni stadio ricostruiva i propri tempi:

| Stadio | Come calcolava i tempi | Conseguenza |
|---|---|---|
| Scene (upload MP3) | ridimensionate in proporzione alla durata | i tagli cadono a caso rispetto alle pause |
| Mix (`sound_designer`) | `adelay=250` fisso se il nome file non conteneva `aligned` | voce spostata di 0,25 s e ultima parola tagliata |
| Caption HyperFrames | finestra della scena divisa in parti uguali | evidenziazione che anticipa o insegue la voce |
| Sottotitoli | testo vecchio dello storyboard | sottotitoli non legati all'audio |
| QC | somiglianza lessicale | "testo coerente" spacciato per "parlato verificato" |
| Export | solo MP4 | nessun cue/SRT/VTT/ASS, nessuna timeline |

## Cosa c'è nella 3.6.6

### 1. `AudioTimelineManager` — una sola verità

`kora_studio/agents/audio_timeline.py` costruisce e valida:

- **regioni di parlato** misurate con `silencedetect` di FFmpeg (energia, non
  riconoscimento): dice *quando* si parla;
- **parole** con `word`, `start_sec`, `end_sec`, `start_frame`, `end_frame`,
  `confidence`, `timing_source`;
- **frasi** (segmenti) con pause reali ≥ 0,45 s e punteggiatura;
- **cue** dei sottotitoli costruiti scena per scena, senza schiacciare il testo;
- **confini di scena sulle pause**: `plan_scene_boundaries()` posa i tagli nel
  punto medio dei silenzi misurati (se le pause non bastano, divide gli
  intervalli più lunghi) e la copertura resta continua.

Tutto finisce in `kora_data/projects/<id>/project_timeline.json` (più
`transcript.json`). Mix, caption, QC ed export leggono quel file.

### 2. Offset dichiarati, niente tagli

`SoundDesigner.voice_manifest()` legge un manifest esplicito:
`is_scene_aligned`, `start_offset_sec`, `end_padding_sec`,
`source_duration_sec`, `effective_duration_sec`, `timeline_duration_sec`,
`end_sec`. Il nome del file non conta più nulla.

Se `start_offset + effective > durata del Reel` viene sollevata
`VoiceTruncationError`: la produzione si ferma con «Voce più lunga del Reel» e
la ragione scritta, invece di consegnare un Reel con l'ultima parola mozzata.

### 3. Caption sui tempi reali

`build_caption_segments_for_scene(..., word_timings=[...])`: quando la timeline
fornisce i tempi, l'evidenziazione parola per parola usa quelli e non la
divisione uniforme. Lo stile grafico resta indipendente dai tempi.

### 4. QC audiovisivo, non lessicale

Nuovi controlli (in `quality_controller.py`):

| Controllo | Tipo | Blocca? |
|---|---|---|
| Voce non tagliata (inizio e fine conservati) | errore | sì, se la voce esce dal Reel |
| Sincronizzazione audio, scene e sottotitoli (cue fuori scena, offset) | errore/avviso | sì sugli errori, no sugli avvisi |
| Trascrizione verificata sulle parole pronunciate | avviso (`non_verificato`) | **no** |

Il terzo esiste proprio per non mentire: senza ASR il risultato è
«non verificato — tempi stimati dentro le pause misurate».

### 5. Artefatti di export

Oltre a MP4 e copertina, in `kora_data/exports/<id>/`:
`_cues.json`, `_sottotitoli.srt`, `_sottotitoli.vtt`, `_sottotitoli.ass`,
`_timeline.json`, `_transcript.json`.

### 6. Editor «Trascrizione & Sincronizzazione»

Nuovo pannello nello Storyboard: allineamento dichiarato, numero di parole e
cue, conflitti, tabella delle frasi con tempi e scena, correzione del testo
(l'audio non viene toccato; ricalcola parole e cue), pulsanti **Ricalcola
allineamento** e **Verifica sincronizzazione**, avviso onesto se manca l'ASR.

### 7. ASR: adapter onesto

`kora_studio/agents/asr.py` preferisce `faster-whisper`, poi `whisper`.
Nessun modello viene scaricato automaticamente. Se nessuno dei due è
installato, `AsrUnavailable` dice all'utente di incollare il copione o di
correggere il testo: **non viene inventata alcuna trascrizione**.

Endpoint: `GET /api/asr/status` (disponibilità reale, livelli
rapido/bilanciato/accurato, comando di installazione).

## Prove eseguite su Linux (questo ambiente)

| Prova | Esito reale |
|---|---|
| `tests/kora_studio/test_v366_audio_timeline.py` | **16 passed** |
| Suite completa `tests/kora_studio` | **263 passed, 3 skipped in 959 s** |
| Audio sintetico 4,2 s (parlato 0,4–1,9 e 3,0–4,2) | regioni rilevate: `(0.4, 1.9)`, `(3.0, 4.2)`; nessuna parola nel silenzio |
| Upload MP3/WAV 12 s su progetto reale (`prj_256e7cbebf`) | storyboard tagliato su 12 scene, somma frame = 300 = 12,00 s esatti |
| Produzione completa dello stesso progetto | render 300 frame, QC: «Voce 0.00s → 12.00s su 12.00s», nessun conflitto |
| Reel finale analizzato con `silencedetect` | parlato 0,61–11,50 s: prima e ultima parola conservate |
| Export | SRT/VTT/ASS/cues/timeline/transcript scritti, 13 cue senza sovrapposizioni |
| `./diagnostica_sync.sh prj_256e7cbebf` | «nessun conflitto · allineamento estimated · parole 35 · cue 13» |
| API | `/timeline`, `/timeline/rebuild`, `PATCH /transcript`, `/sync/verify` (GET+POST), `/asr/status` |

## Non collaudato (dichiarato, non simulato)

- **Motore ASR reale**: in questo ambiente non c'è né `faster-whisper` né
  `whisper`, quindi il percorso `alignment="asr"` è scritto e testato solo
  nella parte di interfaccia (`AsrUnavailable`, stato), **non** su una
  trascrizione vera. I tempi restano stimati dentro le pause misurate.
- **macOS Intel**: qui gira Linux. Le prove di render sono su questa macchina.
- **Diarizzazione / multispaker**: non implementata in 3.6.6; l'audio a due
  voci non viene separato automaticamente.
- **Waveform e multitraccia interattiva**: il pannello mostra frasi e tempi,
  non un editor a tracce con scrubbing audio.
- **Remotion**: continua a non essere installato in questo ambiente.

## Comandi utili

```bash
./diagnostica_sync.sh                      # stato di tutti i progetti
./diagnostica_sync.sh prj_5f62ca8bf7       # un progetto
curl -s -X POST http://127.0.0.1:8020/api/projects/ID/timeline/rebuild \
     -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8020/api/projects/ID/sync/verify
```

## File toccati

- `kora_studio/agents/audio_timeline.py` (nuovo, ~600 righe)
- `kora_studio/agents/asr.py` (nuovo)
- `kora_studio/agents/sound_designer.py` (manifest, filtro voce, nessun 250 ms)
- `kora_studio/agents/voice_director.py` (`voice_manifest` in uscita)
- `kora_studio/agents/director.py` (timeline in produzione, tagli sulle pause, rollback sicuro)
- `kora_studio/agents/hyperframes_captions.py`, `cinematic_editor.py` (tempi reali)
- `kora_studio/agents/quality_controller.py` (3 controlli audiovisivi)
- `kora_studio/agents/export_manager.py` (artefatti + messaggio non duplicato)
- `kora_studio/api/app.py` (5 endpoint, `script` sull'upload, versione 3.6.6)
- `kora_studio/frontend/src/components/SyncPanel.tsx`, `src/lib/api.ts`, `src/app/page.tsx`
- `diagnostica_sync.sh`, `tests/kora_studio/test_v366_audio_timeline.py`
- `setup.sh`, `LEGGIMI.txt`, `INSTALLAZIONE.md`
