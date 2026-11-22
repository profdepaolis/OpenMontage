# KORA STUDIO 3.6.3 — Montaggio robusto (correzione del job in errore)

**Segnalazione:** job `job_8bb556a381` del progetto «La luce si posa» terminato in
**Errore** con `Command [...] timed out after 240 seconds`, mentre lo storyboard era
pronto (8 scene, 718 frame) e tutte le fasi precedenti risultavano completate.

Questo documento spiega la causa reale, cosa è stato cambiato e come riprendere.
Nessuna scorciatoia: il problema era nel renderer, non nell'utente.

---

## 1. Cosa è successo davvero

Il montaggio finale (concat delle scene in cache + sottotitoli + audio) aveva un
**limite di tempo fisso di 240 secondi**, uguale per un reel da 5 secondi e per uno da
30. Su una macchina come quella del Mac in uso, 718 frame a 1080×1920 con otto
sottotitoli non entrano in quattro minuti: FFmpeg viene ucciso a metà e il job finisce
in errore. Le scene erano già in cache e corrette — si è perso solo il montaggio.

C'erano due cause che si sommavano:

1. **Limite fisso.** `run_ffmpeg(cmd, timeout=240)`: nessun rapporto tra lavoro e tempo.
2. **Sottotitoli espansi su tutto il reel.** Ogni PNG di sottotitolo veniva trasformato
   in un flusso `-loop 1 -t 28.72` (718 frame) e poi mascherato con
   `overlay=...:enable='between(t,…)'`. Con otto sottotitoli significa **5.700 frame PNG
   a piena risoluzione decodificati e compositati** invece dei ~250 realmente visibili.

A questo si aggiungeva il preset x264 `medium`, inutile su fotografie con movimento
lento.

---

## 2. Cosa è cambiato in 3.6.3

| Intervento | Effetto |
|---|---|
| **Limite proporzionale al lavoro** | `max(600 s, frame × 1.6)`: 718 frame → **1.149 s**; 2.000 frame → 3.200 s |
| **Limite regolabile** | `KORA_RENDER_TIMEOUT_SEC=1800 ./start.sh` per macchine molto lente |
| **Sottotitoli solo nel loro intervallo** | ogni PNG copre `end − start` secondi e viene spostato con `setpts=PTS+start/TB`; overlay con `eof_action=pass` |
| **Titolo introduttivo** | stessa ottimizzazione (solo la finestra 0,25–2,9 s) |
| **Preset x264** | `veryfast` (configurabile con `KORA_X264_PRESET=fast|medium`), crf invariato (18/19) |
| **Limite per singola scena** | `max(180 s, frame_scena × 4)` invece di 120/180 fissi |
| **Errore parlante** | se il limite viene comunque superato: «Montaggio troppo lento… le scene sono già in cache: premi “Riprova”… alza il limite con KORA_RENDER_TIMEOUT_SEC» |
| **Trasparenza** | il piano di montaggio registra `render_seconds`, `render_timeout_sec`, `x264_preset`; il log del job dice frame, limite e preset prima di partire |

Il testo dei sottotitoli, la tipografia, i tempi e il QC **non** sono stati toccati:
è cambiato solo *come* FFmpeg riceve i livelli.

---

## 3. Misura reale su questa macchina

Prova con la stessa forma del caso segnalato (8 scene, sottotitoli su ogni scena,
1080×1920, 25 fps), codice 3.6.3:

| Voce | Valore |
|---|---|
| Scene | 8 · **625 frame** (25,0 s) |
| Sottotitoli | 7 overlay |
| Clip di scena (cache) | 43,9 s |
| **Montaggio** | **25,9 s** (limite concesso 1.000 s) |
| Totale | 69,8 s |
| Output | 1080×1920 · 25 fps · 25,0 s · 1,84 MB |
| Verifica visiva | sottotitolo corretto a 1,5 s («scena 1») e a 12,0 s («scena 4») |

Su un Mac più lento i tempi crescono, ma il limite cresce con loro: 1.149 s per 718
frame sono circa **4,5 volte** il tempo misurato qui.

---

## 4. Come riprendere il Reel «La luce si posa»

1. Aggiorna con il pacchetto 3.6.3 (comando in fondo).
2. Apri il progetto → **Production**.
3. Premi **Riprova** (in alto a destra) oppure **Produci** dalla Preview.

Le scene sono in `kora_data/projects/prj_d89604e45b/scene_cache/` e vengono riusate:
il nuovo tentativo ripete **solo** il montaggio e il controllo qualità. Non si
rigenerano voce, copione o scene.

---

## 5. Collaudo

- 5 test nuovi in `tests/kora_studio/test_v363_render_speed.py`:
  scala dei tempi, variabile d'ambiente, preset veloce, render reale con durata e
  preset registrati, errore italiano quando il tempo non basta (timeout forzato).
- Suite completa: esito in `docs/KORA_3.6.3_QA.md`.

## 6. Limiti dichiarati

- Il tempo misurato qui è su Linux con ffmpeg di sistema; il valore su macOS dipende
  da CPU, disco e numero di scene. Il limite automatico è dimensionato con un margine
  ampio, non con una misura sul Mac dell'utente.
- Il preset `veryfast` produce file leggermente più grandi a parità di crf (qualche
  punto percentuale): la qualità visiva delle foto resta invariata.
- Se il Mac fosse davvero troppo lento anche con 1.149 s, la strada è alzare
  `KORA_RENDER_TIMEOUT_SEC` (nessun automatismo nasconde il problema).
