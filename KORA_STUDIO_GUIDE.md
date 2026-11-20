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
- **Ideazione, Anti-Ripetizione e Storyboard**: 3 concept distinti e storyboard frame-accurato (es. 20s = 500 frame a 25 fps).
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
