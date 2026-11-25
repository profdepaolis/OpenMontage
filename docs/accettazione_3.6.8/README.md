# Collaudo audiovisivo 3.6.8 — artefatti

Generati da `tools/accettazione_368.py` (Linux, FFmpeg di KORA, motore ASR **assente**).
Report leggibile: `report_accettazione_3.6.8.json`.

| Percorso | Progetto | File |
|---|---|---|
| A · MP3 + copione (`voce_brando.mp3`) | `prj_5bc67a900c` | `prj_5bc67a900c_sottotitoli.srt/.vtt`, `_transcript.json`, `_timeline.json`, frame 6 s e 18 s |
| B · MP3 senza copione (`voce_tiziana.mp3`) | `prj_664458e953` | `prj_664458e953_timeline.json` (nessuna parola, nessuna cue), frame 6 s e 18 s |

Gli MP4 completi sono consegnati separatamente (link nella consegna): i due reel
sono 1080×1920, 25 fps, 21,08 s (527 frame) e 22,05 s (551 frame), audio AAC.

Cosa dicono i file:

* percorso A — la timeline dichiara `alignment="estimated"` con
  `verification.text_status="from_script"`: i tempi vengono dal copione distribuito
  nelle pause misurate, **non** dal riconoscimento del parlato. I sottotitoli
  esportati (14 righe SRT) coincidono con le cue della timeline.
* percorso B — `alignment="none"`: senza motore ASR e senza copione KORA non inventa
  parole. Nessun sottotitolo, reel con sola voce reale, immagini e chiusura brand.

Per il collaudo fonetico installare il motore sul Mac (`./installa_asr.sh`) e ripetere
i due percorsi dal pannello «Trascrizione & Sincronizzazione».
