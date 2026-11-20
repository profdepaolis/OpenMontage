# KORA STUDIO — Piattaforma Privata di Produzione Audiovisiva

**Brand:** KORA Beach Club — Policoro  
**Payoff ufficiale:** `The Art of Being`  
**Formato nativo:** Reel verticali 9:16 (`1080×1920`, `25 fps`, H.264, AAC, Rec.709)

---

## 1. Architettura del Sistema

KORA STUDIO è un'applicazione web privata e modulare costruita sopra l'ecosistema OpenMontage:

- **Backend API & Orchestratore (`kora_studio/api/app.py`, `kora_studio/agents/director.py`)**: FastAPI su SQLite WAL con migrazioni versionate, gestione job osservabile passo-passo (nessuna percentuale inventata), streaming HTTP Range dei video MP4, revisioni in linguaggio naturale con anteprima diff e rigenerazione selettiva.
- **Frontend Web (`kora_studio/frontend/`)**: Next.js 16 + React + TypeScript + Tailwind CSS v4 esportato staticamente (`output: "export"`) e servito direttamente dal server locale KORA STUDIO su `http://127.0.0.1:8000` (con Home conversazionale a 4 azioni, 4 proposte fotografiche selezionabili, parametri avanzati/Hyperframes espandibili e workflow *Suggerisci un tema → Progetta il nuovo Reel → Realizza Reel*).
- **Motore Multi-Agente (`kora_studio/agents/`)**:
  1. `BrandGuardian` — valida testi, prompt e logo originale contro il *Brand Brain* versionato.
  2. `CreativeDirector` + `AntiRepetitionEngine` — integra LLM Provider Manager (Ollama locale gratuito o provider cloud autorizzati, con fallback deterministico offline esplicitamente dichiarato), genera 4 proposte originali (`POST /api/creative/suggest`) o 3 concept da tema libero (`POST /api/creative/from-theme`) e costruisce storyboard narrativi 2.0 frame-accurati (`25 fps`).
  3. `MediaCurator` + `AIMediaLab` — rispetta rigorosamente la cascata di autenticità (*Originale reale → Ritaglio originale → Animazione conservativa → Generazione vincolata → Image-to-Video*), con tracciamento dell'asset di riferimento e budget predefinito `0 €`.
  4. `VoiceDirector` — gestisce voci approvate (`Brando`, `Tiziana`, `Felice`, `Brando & Tiziana` a due voci con assegnazione per scena, `Nessun voiceover`) con normalizzazione EBU R128 (`loudnorm`), pause naturali e sintesi voice-first per allineamento esatto alla timeline.
  5. `Colorist` — preset `KORA Cinematic Identity`, `KORA Experience`, `KORA Seasonal`, `KORA Editorial`, `KORA Custom`, con normalizzazione per clip.
  6. `SoundDesigner` — libreria categorizzata (`kora_studio/vault/sound_library.py`) con traccia CC0 *Next to You — Joth* + 11 SFX costieri/ambient sincronizzati e ducking dinamico sotto la voce.
  7. `CinematicEditor` — composizione deterministica FFmpeg + Pillow + NumPy (con esportazione simultanea della specifica `remotion_composition.json` per Remotion).
  8. `QualityController` — verifica decodifica completa FFmpeg, conteggio reale fotogrammi (`ffprobe -count_frames`), assenza di clipping, coerenza voiceover/sottotitoli, validità temporale, logo originale e payoff esatto `"The Art of Being"`, attivando revisione umana mirata per materiali generativi o `da_verificare`.
  9. `ExportManager` — produce MP4 finale, copertina verticale PNG, caption Instagram + hashtag, report tecnico JSON/Markdown, progetto modificabile JSON e varianti opzionali senza sottotitoli o senza musica.

---

## 2. Installazione, Avvio e Comandi CLI

### macOS / Linux
```bash
# Setup completo (venv Python, FFmpeg/FFprobe statici, build UI, seed asset KORA, diagnostica)
./setup.sh

# Avvio server web + API su http://127.0.0.1:8000 (bind sicuro locale)
./start.sh
```

### Windows (PowerShell)
```powershell
.\start_kora.ps1
```

### Comandi CLI dedicati
```bash
.venv/bin/python -m kora_studio doctor   # Diagnostica FFmpeg, FFprobe, Remotion, SQLite, Provider
.venv/bin/python -m kora_studio seed     # Carica gli asset autentici KORA nel Vault e aggiorna la palette
.venv/bin/python -m kora_studio serve    # Avvia KORA STUDIO (default 127.0.0.1:8000; --host 0.0.0.0 opzionale)
.venv/bin/python -m kora_studio backup   # Crea backup ZIP completo (DB, progetti, export, originali)
.venv/bin/python -m kora_studio e2e --theme "Il piacere di stare insieme davanti al mare." --voice brando --duration 20
```

---

## 3. Stato Operativo Reale e Provider

### Funzionalità già operative al 100% in locale (0 €)
- **Media Vault**: ingestione con hash SHA-256, deduplicazione, copie originali immutabili, copie di lavoro, miniature e ricerca semantica in italiano/inglese.
- **KORA Brand Brain & Brand Guardian**: palette estratta realmente dalle foto autentiche KORA, controllo anti-marketing aggressivo, protezione logo originale `LOGO KORA.PNG` + payoff `The Art of Being`.
- **Ideazione, Anti-Ripetizione e Storyboard**: 4 proposte narrative originali selezionabili (o 3 concept da tema libero) con anti-ripetizione sullo storico e storyboard narrativo frame-accurato (es. 20s = 500 frame a 25 fps).
- **AI Media Lab (Modi locali)**: ritaglio 9:16 ad alta risoluzione da dettagli reali, animazioni fotografiche deterministiche (*push-in*, *pull-out*, *pan*, *tilt*, *micro-parallax*) e scena illustrativa di apertura etichettata.
- **Voice Studio (Tracce reali + fallback sicuro)**: riproduzione fedele delle registrazioni originali `voce_brando.mp3` e `voce_tiziana.mp3` sincronizzate e normalizzate (`loudnorm`); se vengono richiesti testi nuovi non coperti dalle registrazioni e nessun modello TTS è configurato, il sistema segnala esplicitamente la necessità di sintesi/registrazione senza sostituire silenziosamente la voce.
- **Montaggio, Color Grading, Sottotitoli, Sound Design, Logo Outro ed Export**: rendering MP4 H.264 1080×1920 25 fps Rec.709 verificato tramite `ffprobe`, con copertina verticale, caption Instagram e report tecnico.
- **Revisione in Linguaggio Naturale, Versioning, Rollback e Backup**: diff preview, rigenerazione selettiva delle sole scene modificate, snapshot e backup ZIP con `PRAGMA integrity_check`.

### Connettori AI Esterni (stato attuale e configurazione)
Tutti i connettori esterni sono registrati in `kora_studio/providers/registry.py` con budget predefinito **`0.00 €`** e limite massimo configurabile **`20.00 € / mese`**:

| Provider | Capacità | Stato predefinito | Crediti | Note |
|---|---|---|---|---|
| `local_ffmpeg_pillow` | Video editing, animazione foto, color, analisi | **Operativo** (locale, gratuito) | Illimitato | Motore principale attivo di default |
| `piper_tts_local` | Sintesi vocale offline gratuita | In attesa modello `.onnx` | Illimitato | Inserire file `.onnx` italiano in `kora_data/voices/piper/` |
| `remotion_local` | Rendering composizioni React/Remotion | Specifica pronta; richiede Chromium | Illimitato | Esporta sempre `remotion_composition.json`; usa FFmpeg finché Chromium non è installato |
| `gemini_api` | LLM, analisi visiva, immagini | Non configurato | `Non verificabile` | Inserire `GEMINI_API_KEY` nella sezione *AI Providers* |
| `google_veo_api` | Image-to-Video / Text-to-Video | Non configurato | `Non verificabile` | Richiede `GEMINI_API_KEY` e autorizzazione spesa |
| `elevenlabs_api` | Voci premium e clonazione autorizzata | Non configurato | `Non verificabile` | Richiede `ELEVENLABS_API_KEY`; verifica saldo reale via API |
| `openai_api` | LLM, TTS, Whisper, immagini | Non configurato | `Non verificabile` | Richiede `OPENAI_API_KEY` (ChatGPT Plus non include API) |
| `leonardo_ai` | Generazione / editing immagini / I2V | Non configurato | `Non verificabile` | Richiede `LEONARDO_API_KEY` |
| `kling_ai` / `runway_api` | Image-to-Video / Text-to-Video | Non configurato | `Non verificabile` | Richiede API key ufficiale e autorizzazione spesa |

Tutte le API key inserite dall'interfaccia web vengono cifrate localmente tramite `SecretVault` (AES/CTR-SHA256 + HMAC-SHA256) usando `.kora_master.key` (escluso da Git).

---

## 4. Test Automatizzati

```bash
.venv/bin/python -m pytest tests/kora_studio/ -v
```

La suite verifica:
1. Versioning/rollback del Brand Brain e derivazione reale della palette dai materiali KORA.
2. Blocco copie promozionali aggressive e tutela del logo originale da parte del Brand Guardian.
3. Immutabilità originali, deduplicazione SHA-256 e ricerca semantica nel Media Vault.
4. Cifratura chiavi API (`SecretVault`), budget predefinito 0 € e tetto massimo 20 € (`CostGuard`).
5. Selezione a costo minimo nel Provider Manager e assenza di crediti inventati (`Non verificabile`).
6. Generazione di 3 concept distinti e rilevamento ripetizioni (`AntiRepetitionEngine`).
7. Ciclo API completo end-to-end: creazione progetto, produzione di un Reel reale `1080×1920`, `25 fps`, `20.0s`, revisione selettiva in linguaggio naturale, rollback e backup ZIP.
8. Upgrade 2.0 — 11 scenari di accettazione (`tests/kora_studio/test_upgrade_2_acceptance.py`): 4 proposte originali + separazione Hyperframes, tema libero fuori template, Reel 20s brand identity con matematica dei frame, Reel 30s stagionale, due voci `brando_tiziana`, Reel senza media nuovi a budget 0 €, fallback offline con provider esterni indisponibili, revisione di una singola scena + restore di versione, export e verifica video reale (`ffprobe -count_frames`, QC 17/17), sicurezza endpoint sensibili + Vault multimodale.

Risultato attuale: **22/22 test verdi** (~6 minuti).

### Sicurezza degli endpoint sensibili
Per impostazione predefinita KORA STUDIO ascolta solo su `127.0.0.1` e gli endpoint sensibili (credenziali, budget, backup, generazione) accettano esclusivamente client locali con `Host` `127.0.0.1`/`localhost`/`::1` e `Origin` locale (protezione anti-CSRF/anti-DNS-rebinding).

Per esporre temporaneamente la UI su un tunnel o una preview remota, **senza indebolire il default**, esistono tre estensioni opt-in via ambiente:

```bash
KORA_TRUSTED_HOSTS="kora.miodominio.it"      # host esatti aggiuntivi
KORA_TRUSTED_HOST_SUFFIXES=".e2b.app"        # suffissi di dominio fidati (preview)
KORA_ALLOWED_ORIGIN="https://kora.miodominio.it"  # origini CORS aggiuntive
```

Senza queste variabili qualsiasi `Host`/`Origin` diverso dal locale resta bloccato con `403`.

---

## 5. Messaggi frequenti (e cosa significano davvero)

| Messaggio | Significato | Azione |
|---|---|---|
| `Google Gemini: 404 … generateContent` | Il modello configurato non è abilitato per la tua chiave/progetto. | Nessuna: KORA interroga `ListModels`, prova in catena i modelli disponibili, memorizza quello funzionante e lo mostra in *AI Providers*. |
| `Motore deterministico locale KORA attivo (0 €)` | Nessun LLM esterno ha risposto: le proposte restano valide, generate offline. | Apri «dettagli motore» sotto il titolo delle proposte per la causa esatta. |
| `ElevenLabs … paid_plan_required / library voices (402)` | Il piano Free non consente le voci della *libreria* via API. | KORA passa automaticamente a una voce `premade`/`clonata` del tuo account; per usare la voce esatta serve un Voice ID utilizzabile o un piano superiore. |
| `Fallback utilizzato: Piper TTS locale gratuito` | Sintesi offline gratuita, nessun costo, timbro più sintetico. | Per la voce artistica: Voice ID ElevenLabs valido, OpenAI TTS, oppure registrazione originale in *Media Vault*. |
| `Avvisi · fallback automatici` (badge ambra in Production) | Il job è proseguito correttamente sostituendo un servizio. | Rosso = bloccante; ambra = sostituzione automatica già gestita. |

---

## 6. Voce: flusso manuale (ElevenLabs, senza API a pagamento)

Se il piano ElevenLabs non consente le voci di libreria via API (errore `402 paid_plan_required`), puoi comunque
ottenere la voce artistica **senza spendere nulla in API**:

1. **Preview → Voce del Reel → «1. Copia copione»** (oppure «Scarica .txt»): il testo parlato, scena per scena.
2. **«2. Genera sul sito»**: incolla il copione su `elevenlabs.io/app/speech-synthesis`, scegli la voce
   (anche una voce di *libreria*, che sul sito è utilizzabile) e scarica l'MP3.
3. **«3. Carica MP3»**: KORA importa il file, **riallinea automaticamente le scene alla durata reale** della voce
   (frame esatti a 25 fps) e lo usa al posto della sintesi automatica. Poi «Realizza Reel».

Endpoint corrispondenti:
`GET /api/projects/{id}/voiceover/script` · `POST /api/projects/{id}/voiceover/upload` (multipart `file`) ·
`DELETE /api/projects/{id}/voiceover` (torna alla sintesi automatica).

### Perché non VibeVoice (per ora)
VibeVoice (Microsoft Research) è MIT ma **solo ricerca**, addestrato su **inglese e cinese**, richiede **GPU NVIDIA**
(circa 7-8 GB VRAM per il modello 1.5B, 17-24 GB per il 7B). Su un MacBook Pro Intel (solo CPU) l'inferenza sarebbe
di minuti per secondo di audio, senza supporto italiano. Alternative già integrate: **Piper TTS italiano** (offline,
0 €, CPU) e appunto l'import manuale da ElevenLabs.

---

## 7. Organizzazione dei progetti e materiali

- **Nuovo Reel con materiali scelti**: *Home → Parametri avanzati* → sezione **Materiali del Reel**: selezioni foto/video
  dal Vault (se non selezioni nulla, decide il MediaCurator). La selezione **vincola** lo storyboard a quei soli asset.
- **Cartella di destinazione**: nello stesso pannello puoi indicare un percorso assoluto (es. `~/Desktop/KORA-REELS`):
  a render completato KORA copia automaticamente MP4, copertina, caption, report e progetto JSON in
  `<cartella>/<titolo>_<idprogetto>/`.
- **Archiviare** (*Progetti → Archivia…*): esporta il pacchetto nella cartella scelta e marca il progetto come
  `Archiviato` — sparisce dall'elenco attivo ma resta ripristinabile con *Ripristina*.
- **Eliminare** (*Progetti → Elimina*): rimuove definitivamente progetto, versioni, job collegati e file generati.
  Bloccato se c'è un job in corso su quel progetto.
- **Filtro voci ElevenLabs** (*AI Providers → Mappatura Voci*): selettori **Solo italiane / Tutte le lingue** e
  **Solo premade / Premade + clonate / Tutte**. Le voci premade sono etichettate in inglese ma, con
  `eleven_multilingual_v2`, parlano italiano.

Endpoint: `POST /api/projects/{id}/export-to` · `POST /api/projects/{id}/archive` · `POST /api/projects/{id}/unarchive` ·
`DELETE /api/projects/{id}` (**solo da host locale**).
