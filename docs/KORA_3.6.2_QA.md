# KORA STUDIO 3.6.2 — Rapporto di collaudo

Data: 22 novembre 2026
Ambiente: Linux (sandbox di collaudo), Python 3.11, ffmpeg/ffprobe di sistema (n9.0.2),
Node 22, Next.js 16.4.6
Branch: `arena/36db4987-openmontage`

---

## 1. Suite automatica

```
.venv/bin/python -m pytest tests/kora_studio -q
234 passed, 3 skipped in 1058.66s (0:17:38)
```

I 3 test saltati sono condizioni d'ambiente preesistenti (provider reali non
configurati), non nuovi buchi: nessun test è stato disattivato per far quadrare il
risultato. I 14 test nuovi sono in `tests/kora_studio/test_v362_media_sources.py`.

## 2. Verifica d'installazione

```
./verifica.sh
esiti: 38 OK · 0 KO · 2 avvisi
```

- Avviso 1: spazio disco 569 MB liberi durante il collaudo (i render di prova occupano
  spazio; su una macchina normale il valore è più alto).
- Avviso 2: porta 8020 occupata da un server di collaudo già attivo — atteso.

Le tre verifiche aggiunte in 3.6.2: script `diagnostica_motori.sh` eseguibile,
motori censiti con render FFmpeg pronto, dodici motori dichiarati separatamente.

## 3. Interfaccia compilata

```
npx tsc --noEmit        → nessun errore
npm run build           → ✓ Compiled successfully · ✓ Generating static pages (3/3)
```

I nuovi componenti (`MediaSourceWizard`, `EnginesInventory`, `SceneMaterialsPanel`)
sono in chunk separati: il caricamento è davvero su richiesta. L'export statico in
`kora_studio/frontend/out` è quello incluso nel pacchetto.

## 4. Prova end-to-end via HTTP (server reale, porta 8031)

Flusso eseguito con `curl`, come farebbe il browser:

| Passo | Esito |
|---|---|
| `POST /api/vault/upload` (3 foto + logo) | 200, 4 asset importati, 0 errori |
| `POST /api/creative/project-reel` (`vault_only`, 3 asset) | 200, progetto con 5 scene |
| `GET /api/projects/{id}/media-selection` | `vault_only`, 3 asset, `Solo Media Vault` |
| `GET /api/projects/{id}/scenes` | 5 scene, materiali dall'archivio scelto |
| `GET .../scenes/1/alternatives` | 200, 4 alternative con miniatura reale |
| `PATCH .../media-selection` (`mixed`, `archive_first`) | 200, `mixed` · `vault_first` · nessuna rigenerazione |
| `POST .../scenes/2/media` (caricamento inline) | 200, asset importato e assegnato alla sola scena 2 |
| `GET /api/media/engines` | 200, 12 motori, `render_ready: true` |
| `POST /api/media/engines/remotion/test` | 200, `ok: false`, «dipendenze Remotion non installate» |
| `GET /api/media/voices` | 200, 5 voci dichiarate + 5 motori TTS con disponibilità reale |

## 5. Prova di produzione reale (FFmpeg)

Reel da 12 s con materiali dell'archivio, logo autentico, pipeline di produzione
completa (`run_production_pipeline`, sottotitoli inclusi):

| Metrica | Valore |
|---|---|
| Stato job | Completato |
| File | `…_reel_final.mp4`, 1,99 MB |
| Risoluzione | 1080 × 1920 |
| Frame rate | 25 fps |
| Durata | 12,00 s (attesa 12,00 s) |
| Frame contati | 300 (attesi 300) |
| Audio | AAC, −14,6 LUFS, true peak −4,6 dBTP, nessun clipping |
| Motore di render | `ffmpeg_deterministic_25fps` |
| Caption | `kora_native`, stile `serif_minimal`, vignetta `soft_luxury` |
| QC | 17 superati · 1 non applicabile · 0 non superati |

L'unico controllo non applicabile è «Sottotitoli sincronizzati»: in quella prova non
c'era voiceover. Non è un successo mascherato: è la dichiarazione corretta.

## 6. Collaudo A–L

| # | Prova | Esito |
|---|---|---|
| A | Reel solo da archivio | superato |
| B | Solo materiali nuovi | superato |
| C | Archivio + nuovi | superato |
| D | Sostituzione foto in montaggio | superato |
| E | Movimento di camera di una scena | superato |
| F | Voiceover con motore reale | **non collaudato** (nessun TTS installato) |
| G | Trascrizione Whisper | **non collaudato** (Whisper assente) |
| H | Animazione HyperFrames interna | superato (preset interni applicati al render) |
| I | Render FFmpeg completo | superato |
| J | Composizione Remotion reale | **non collaudato** (dipendenze npm assenti) |
| K | Export 1080×1920 con QC | superato |
| L | Modifiche selettive senza rigenerare | superato |

Dettaglio e metodo: `docs/KORA_3.6.2_RESTAURO_AUDIOVISIVO.md`.

## 7. Cosa non è stato possibile collaudare qui

- Voce reale (ElevenLabs, Piper, Edge, Qwen3, Chatterbox): nessun motore installato.
- Whisper: nessun pacchetto.
- Remotion: Composer presente, dipendenze npm assenti.
- HyperFrames esterno: nessun endpoint configurato.
- Comportamento su macOS: il collaudo è avvenuto su Linux. Gli script (`start.sh`,
  `installa`, `ripristina.sh`) sono gli stessi della 3.6.1, già usati sul Mac.

Nessuna di queste voci è dichiarata funzionante in qualche punto dell'interfaccia.
