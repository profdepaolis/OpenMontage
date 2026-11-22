# KORA STUDIO 3.6.2 — Restauro funzionale e audiovisivo

**Base:** 3.6.1 (commit `56ea9ce`) + aggiornatore `aggiorna_kora.sh` (`b6f0729`)
**Rilascio:** 3.6.2 — 22 novembre 2026
**Marchio:** KORA STUDIO — *The Art of Being* · Policoro

Questo documento dichiara, senza abbellimenti, cosa è stato ripristinato, cosa è stato
realmente verificato su una macchina e cosa **non** è stato collaudato. Le voci
«non collaudato» non sono un dettaglio: sono la parte onesta del rapporto.

---

## 1. Cosa cambia, in una pagina

| Ambito | 3.6.1 | 3.6.2 |
|---|---|---|
| Navigazione | 4 voci (Studio, Archivio, Progetti, Impostazioni) | **10 sezioni**: Home · Progetti · Media Vault · Creative Studio · Production · Preview · Storyboard · AI Providers · Brand Profile · Export |
| Provenienza materiali | implicita, sempre `vault_auto` | **scelta esplicita A/B/C** prima di progettare, **salvata nel progetto** |
| Materiali per scena | menu a tendina sull'archivio | **pannello scena**: sostituzione, alternative autentiche, caricamento inline, punto focale, movimento di camera, anteprima, blocco |
| Motori | presenze dichiarate nel codice | **inventario reale** di 12 motori, con test locali separati e motivo esplicito quando manca qualcosa |
| Diagnostica | solo `diagnostica_ai.sh` (motore creativo) | anche `diagnostica_motori.sh` (motori audiovisivi, nessuna installazione) |
| Caricamento | sezioni caricate insieme | componenti e dati **su richiesta**, nessuna scansione all'avvio |

Nessuna funzione è stata rimossa: Model Manager, cache, OpenCode, protezioni economiche,
Quality Check, versioning, backup restano al loro posto.

---

## 2. Navigazione: le dieci sezioni

La barra laterale è divisa in tre gruppi, tutti raggiungibili con un clic:

- **Studio** — Home (ingresso semplificato), Progetti, Media Vault, Creative Studio
- **Lavorazione** — Production, Preview, Storyboard, Export
- **Sistema** — AI Providers, Brand Profile

Le schermate prima sepolte dentro «Studio» (Storyboard, Production, Preview, Export)
hanno ora una voce propria. Non esiste codice di produzione duplicato: ogni sezione
riusa il componente già presente (`SceneEditor`, `ProductionView`, `PreviewView`,
`ExportView`, `CreativeStudioView`, `MediaVaultView`, `BrandProfileView`).
I componenti pesanti sono caricati con `next/dynamic`: il bundle iniziale contiene
solo la Home.

Nella sezione **AI Providers** convivono quattro schede, tutte reali:
«Motori locali» (inventario), «Motore AI» (collegamento e Model Manager),
«Modelli & benchmark», «Provider & budget».

---

## 3. Nuovo Reel: la scelta A/B/C

Appena si preme **Progetta il nuovo Reel** o **Realizza Reel**, si apre la domanda
esplicita:

> **DA DOVE PRENDIAMO I MATERIALI?**

- **A — Solo dal Media Vault**: filtri Tutti/Foto/Video, ricerca per nome o categoria,
  miniature reali (`/api/vault/{id}/thumb`), selezione multipla, oppure
  «scegli automaticamente i materiali migliori».
- **B — Solo materiali nuovi**: caricamento multiplo di foto e video, miniature dei file
  appena importati; gli originali vengono copiati nel Vault e il Reel usa **solo** quelli.
- **C — Archivio + nuovi**: entrambi, con priorità dichiarata («prima l'archivio» oppure
  «prima i nuovi») e nessun doppione.

La scelta viene scritta nel progetto (`preferences.media_selection` e
`preferences.selected_asset_ids`) e **non viene mai riportata a `vault_auto`**.
Il motore creativo la rispetta: con una modalità vincolante l'inventario degli asset
disponibili è limitato a quelli scelti, e se un asset richiesto non esiste la
progettazione si ferma con un messaggio chiaro invece di allargarsi all'archivio intero.

API: `GET|PATCH /api/projects/{id}/media-selection`
(`mode`: `vault_only` · `upload_only` · `mixed` · `vault_auto`; sinonimi storici
`selected_only`, `combined`, `uploads`, `allow_crops` letti e tradotti).

---

## 4. Materiali e regia per singola scena

Nello Storyboard, ogni scena ha il pannello **«Materiali e regia»**:

| Azione | Come |
|---|---|
| usare una foto o un video dell'archivio | menu «Materiale» (già presente, ora affiancato dal pannello) |
| caricare un nuovo materiale **dalla scena** | «Carica nuovo» → `POST /api/projects/{id}/scenes/{n}/media` (import nel Vault + assegnazione immediata) |
| sostituire l'asset | stessa route, oppure scelta dal menu |
| cercare alternative autentiche | «Alternative» → `GET /api/projects/{id}/scenes/{n}/alternatives` (stesso tipo di materiale, escluso quello in uso) |
| modificare il punto focale | griglia 3×3 → `crop_focus {x, y, zoom}` |
| modificare il movimento di camera | select → `camera_motion` (+ `motion_intensity`) |
| vedere l'anteprima della sola scena | «Anteprima scena» → `POST /api/projects/{id}/scenes/{n}/preview` |
| bloccare l'asset approvato | «Blocca approvata» → `POST /api/projects/{id}/scenes/{n}/locks` |

Ogni modifica passa da `scene_editor.update_scene`: **non** rigenera voce, copione o
altre scene. Una scena bloccata rifiuta le modifiche (409) invece di accettarle in
silenzio.

«Genera variante autorizzata» resta nel Visual Director esistente: non è stato
aggiunto un secondo percorso di generazione.

---

## 5. Inventario reale dei motori

`GET /api/media/engines` (e `python -m kora_studio.media.engines`) elencano dodici
motori, ognuno con: installato, configurato, disponibile, esito del test, motivo,
risorse richieste. **Un nome nel codice non è una disponibilità**: senza file o
pacchetto il motore risulta non installato e il motivo viene scritto per esteso.

Risultato reale su questa macchina di collaudo (Linux, ffmpeg di sistema):

```
KORA STUDIO — motori sulla macchina
motore                  stato                        dettaglio
FFmpeg                  disponibile                  ffmpeg version n9.0.2
FFprobe                 disponibile                  ffprobe version n9.0.2
Remotion (Composer)     non installato               dipendenze Remotion non installate (npm install)
HyperFrames interno     disponibile                  6 preset caricati
HyperFrames esterno     non installato               KORA_HYPERFRAMES_URL non impostata
Whisper (OpenAI)        non installato               pacchetto whisper non installato
faster-whisper          non installato               pacchetto faster-whisper non installato
Chatterbox TTS          non installato               pacchetto chatterbox non installato
ElevenLabs              non installato               chiave ElevenLabs non configurata
Piper TTS (locale)      non installato               binario piper assente; nessuna voce .onnx
Edge TTS                non installato               pacchetto edge-tts non installato
Qwen3-TTS               non installato               pacchetto/modello Qwen3-TTS assente
3/12 disponibili · render FFmpeg: pronto
```

Sul Mac dell'utente l'elenco cambierà: è esattamente ciò che serve. I test sono
**facoltativi** (`diagnostica_motori.sh --test`, oppure il pulsante «Verifica ora» nella
sezione AI Providers) e **non installano nulla**: nessun download automatico, nessuna
spesa, nessuna chiamata di rete per ElevenLabs.

---

## 6. FFmpeg come renderer principale

Il renderer FFmpeg non è stato riscritto: è stato verificato. La prova end-to-end di
questo rilascio ha prodotto un Reel completo con materiali reali dell'archivio di prova:

- output 1080×1920, 25 fps, H.264 + AAC
- durata esatta **12.00 s**, **300 frame** contati con `nb_read_frames`
- loudness −14.6 LUFS, true peak −4.6 dBTP, nessun clipping
- **17 controlli QC superati, 1 non applicabile** (sincronizzazione sottotitoli: nessun
  voiceover in quella prova)

Il piano di montaggio dichiara `render_engine: ffmpeg_deterministic_25fps`,
`caption_engine: kora_native`, con i preset HyperFrames interni applicati
(`serif_minimal`, vignetta `soft_luxury`).

---

## 7. Remotion, HyperFrames, Whisper, voci

### Remotion
Il Composer è presente nel repository (`remotion-composer/`), ma le dipendenze npm
**non** sono installate: `remotion_composition.json` viene scritto come specifica e
**non** è una produzione Remotion. L'inventario riporta «non installato» con il motivo.
La procedura di installazione/ripristino resta quella esistente e reversibile
(`npm install` dentro `remotion-composer`, verifica del runtime); non viene eseguita
automaticamente e non tocca FFmpeg. Quando il runtime sarà operativo, il renderer
Remotion prenderà le scene dal medesimo storyboard.

### HyperFrames
`hyperframes_captions.py` e tutti i preset (kinetic, glass, word highlight, editorial
titles, intro, grain, vignette, motion pace, payoff, logo outro) sono intatti.
«HyperFrames interno» e «HyperFrames esterno» sono **due voci distinte**: i preset
locali non dimostrano in alcun modo che un servizio esterno sia collegato.

### Whisper
Nessun pacchetto Whisper o faster-whisper è installato su questa macchina: la voce
resta «non installata» e la trascrizione di un voiceover importato **non è collaudata**.
Il Voice Director continua a sincronizzare i copioni senza Whisper, quindi la sua
assenza non blocca la produzione.

### Analisi di compatibilità (non collaudo)

Analisi tecnica, non esecuzione: nessuno di questi pacchetti è stato installato.

| Motore | Requisiti reali | Nota per il Mac |
|---|---|---|
| **Chatterbox TTS** | Python ≥ 3.11, PyTorch, pesi del modello (~2–4 GB), RAM 8 GB+ | su Apple Silicon gira su MPS ma più lento della GPU; non adatto all'avvio automatico |
| **Piper** | binario `piper` + una voce `.onnx` italiana in `kora_data/voices/piper` | il più leggero: nessun Python extra, avvio istantaneo |
| **Edge TTS** | pacchetto `edge-tts` e rete verso il servizio Microsoft | richiede connessione; nessuna chiave |
| **ElevenLabs** | chiave API e voce autorizzata | a consumo: resta dietro autorizzazione esplicita |
| **Qwen3-TTS** | runtime e pesi specifici, GPU consigliata | compatibilità non verificata: da valutare solo se già presente |
| **Whisper / faster-whisper** | `openai-whisper` (+ ffmpeg) oppure `faster-whisper` (+ CTranslate2) | faster-whisper è più leggero su CPU; entrambi sono opzionali |

Regola: **nessuna installazione pesante automatica**. Si installa solo su richiesta
esplicita, e solo dopo aver letto questa tabella.

### Voci (TTS)
Sono elencati e verificati separatamente ElevenLabs, Piper, Edge TTS, Qwen3-TTS e
Chatterbox. Su questa macchina **nessuno** è disponibile (chiavi assenti, pacchetti
assenti, voci Piper assenti): `GET /api/media/voices` lo dice e non propone scorciatoie.
Brando e Tiziana restano operativi solo con registrazione autentica nel Vault o voce
ElevenLabs autorizzata; **nessuna voce sintetica sostitutiva viene chiamata Brando**.
La modalità «senza voiceover» e l'importazione di una registrazione restano attive.

---

## 8. Collaudo A–L

Metodo: test automatici reali (`tests/kora_studio/test_v362_media_sources.py`), più una
prova end-to-end di produzione con FFmpeg sul campo. Nessun test usa mock per dichiarare
un risultato: dove servono esecuzioni reali, sono reali.

| # | Prova | Esito | Come è stata verificata |
|---|---|---|---|
| A | Reel solo da archivio | **superato** | inventario limitato agli asset scelti; progetto con `vault_only` e asset memorizzati |
| B | Solo materiali nuovi | **superato** | upload reale → `upload_only` → progetto con il nuovo asset; richiesta vuota respinta con 400 |
| C | Archivio + nuovi | **superato** | PATCH `mixed` con priorità `uploads_first`; modalità vincolante vuota respinta; scelta precedente conservata |
| D | Sostituzione foto in montaggio | **superato** | PATCH scena: cambia solo l'asset della scena; testi, movimenti e altre scene identici; nessun job avviato |
| E | Movimento di camera di una scena | **superato** | PATCH `camera_motion`; le altre scene restano invariate |
| F | Voiceover con motore davvero disponibile | **non collaudato** | nessun motore TTS installato in questa macchina; la produzione è stata provata senza voce |
| G | Trascrizione Whisper | **non collaudato** | Whisper/faster-whisper assenti: l'inventario lo dichiara |
| H | Animazione HyperFrames interna | **superato (preset interni)** | 6 preset caricati; il render applica caption e vignetta `soft_luxury` con motore nativo |
| I | Render FFmpeg completo | **superato** | 12.00 s, 1080×1920, 25 fps, H.264/AAC, 300 frame esatti |
| J | Composizione Remotion reale | **non collaudato** | Composer presente, dipendenze npm assenti: specifica scritta, non renderizzata |
| K | Export 1080×1920 con QC | **superato** | 17 controlli superati, 1 non applicabile, file 1.9 MB |
| L | Modifiche selettive senza rigenerare | **superato** | D + E + scena bloccata che rifiuta la modifica |

Suite completa: vedi `docs/KORA_3.6.2_QA.md` (esito registrato dopo l'esecuzione).

---

## 9. Aggiornamento sicuro da 3.6.1

L'aggiornatore è lo stesso (`aggiorna_kora.sh`), con il pacchetto 3.6.2:

```bash
curl -fsSL "https://drive.google.com/uc?export=download&id=1rC7_h2xNKf6n-1aHeRgzFHYF-8bqOIIq" -o /tmp/aggiorna_kora.sh && bash /tmp/aggiorna_kora.sh
```

Se il pacchetto l'hai già scaricato a mano:

```bash
curl -fsSL "https://drive.google.com/uc?export=download&id=1rC7_h2xNKf6n-1aHeRgzFHYF-8bqOIIq" -o /tmp/aggiorna_kora.sh \
  && bash /tmp/aggiorna_kora.sh --da-zip ~/Downloads/KORA_Studio_Light_3.6.2_installer.zip
```

**Pacchetto 3.6.2:** `KORA_Studio_Light_3.6.2_installer.zip` —
62 813 998 byte · md5 `a10a7c9aaf58d2d744c2bfe4b924c292` —
<https://drive.google.com/file/d/1aHGOegELmKTVe1okKkymqdTfN5fS5Okw/view>

- backup del database **prima** di ogni modifica (cartella `kora_data/backups`)
- `kora_data/` (database, Vault, progetti, export) non viene mai sostituita
- nessuna installazione pesante automatica
- avvio: `./start.sh` → `http://127.0.0.1:8020`

---

## 10. Limiti dichiarati

1. Il collaudo è stato eseguito su Linux con ffmpeg di sistema, non sul Mac dell'utente:
   il comportamento su macOS resta da confermare sul campo.
2. Il motore creativo AI non è autenticato in questo ambiente: le prove usano il
   percorso deterministico (stesso codice, nessuna chiamata a provider). Le prove A–E
   riguardano selezione e montaggio, non la qualità dei testi generati dall'AI.
3. Remotion, Whisper, tutti i TTS e HyperFrames esterno restano **non collaudati**
   perché non installati. Nessuna scorciatoia è stata presa per farli sembrare attivi.
4. Il pannello «alternative» propone materiali autentici dell'archivio per tipo di
   materiale: non valuta la somiglianza semantica con un modello visivo.
