# KORA STUDIO 3.6.3 — Rapporto di collaudo

Data: 22 novembre 2026
Base: 3.6.2 · correzione del job di produzione interrotto da `timed out after 240 seconds`.

---

## 1. Suite automatica

```
.venv/bin/python -m pytest tests/kora_studio -q
239 passed, 3 skipped in 882.89s (0:14:42)
```

5 test nuovi in `tests/kora_studio/test_v363_render_speed.py`:

| Test | Cosa verifica |
|---|---|
| `test_render_timeout_scales_with_frames` | 300 frame → 600 s, 718 → 1.149 s, 2.000 → 3.200 s |
| `test_render_timeout_env_override` | `KORA_RENDER_TIMEOUT_SEC`, valori assurdi ignorati, minimo 60 s |
| `test_x264_preset_is_fast_by_default` | preset `veryfast` (o quello scelto da ambiente) |
| `test_render_reel_records_time_and_preset` | render reale 1080×1920 con sottotitoli; file valido, `render_seconds`, `render_timeout_sec`, `x264_preset` |
| `test_render_timeout_gives_actionable_italian_error` | timeout forzato a 1 s → messaggio «Montaggio troppo lento… Riprova… KORA_RENDER_TIMEOUT_SEC» |

I 3 test saltati sono le condizioni d'ambiente preesistenti (provider reali non
configurati), non nuove esclusioni.

## 2. Misura del montaggio (stessa forma del caso segnalato)

8 scene · 625 frame · 1080×1920 · 25 fps · 7 sottotitoli sovrapposti:

| Voce | Valore |
|---|---|
| Clip di scena | 43,9 s (poi in cache) |
| Montaggio | **25,9 s** con limite concesso 1.000 s, preset `veryfast` |
| Totale | 69,8 s |
| Output | 1080×1920 · 25,0 s · 1,84 MB |
| Controllo visivo | fotogramma a 1,5 s = «scena 1»; a 12,0 s = «scena 4» — tempi corretti |

## 3. Verifica d'installazione

`./verifica.sh` → 38 controlli (i 37 storici + i 3 della diagnostica motori
introdotti in 3.6.2), esito senza KO. Il dettaglio è nel report della 3.6.2.

## 4. Cosa non è stato possibile collaudare

- Il tempo di montaggio **su macOS**: il collaudo gira su Linux. Il limite è
  dimensionato su una misura (718 frame ⇒ 1.149 s) con margine, non su una prova
  sul Mac in uso.
- Il confronto percettivo `veryfast` vs `medium`: non è stato fatto un test visivo
  comparativo; il crf resta 18/19 e il QC tecnico passa.
- Remotion, Whisper e i motori TTS restano **non collaudati** (non installati),
  come dichiarato nella 3.6.2.
