# KORA 3.6 — Rapporto di collaudo (QA)

Data: 22 novembre 2026 · build 3.6.0 · ambiente Linux (sandbox), FFmpeg 6.x via
binari del progetto.

## 1. Suite automatizzata

```text
.venv/bin/python -m pytest tests/kora_studio -q
209 passed, 3 skipped in 900,65 s (15:00)
```

- 178 test ereditati dalla 3.5 (tutti verdi);
- 19 nuovi test in `test_v36_model_manager.py` (errori provider, fail-fast, Model Manager);
- 12 nuovi test in `test_v36_consolidation.py` (stati QC, loudness, blocchi, manifest
  voce, ripristino, budget ideazione);
- 3 test saltati: richiedono provider o risorse esterne, dichiarati nel codice.

Classificazione: **unit + integration reali** su filesystem e FFmpeg reali;
**mock** solo per il CLI OpenCode assente; nessun test «dichiarato verde per
costruzione».

## 2. Controlli di installazione

```text
./verifica.sh
36 OK · 0 KO · 0 avvisi   (server fermo)
35 OK · 0 KO · 1 avviso   (server attivo: porta 8020 occupata, non è un guasto)
```

Nuovi controlli 3.6: Model Manager importabile con timeout 45 s di default,
quattro stati QC, `motion_speed` nei blocchi regia, `ripristina.sh` eseguibile,
master audio a −14 LUFS, versione 3.6.0 registrata.

## 3. Collaudo funzionale eseguito (reale)

| Prova | Esito | Dati |
|---|---|---|
| Avvio server e health | OK | `/api/health` risponde, payoff «The Art of Being» |
| Configurazione modelli via API | OK | ruoli e tempi corretti, persistenza verificata |
| Ricerca modelli senza CLI | OK (onesto) | `total: 0`, errore «opencode non trovato nel PATH» |
| Diagnostica modelli | OK | `installed: false`, nessun falso positivo |
| Progettazione Reel 20 s | OK | 8 scene, 500 frame |
| Produzione Reel 20 s | OK | 168,4 s, QC 17 superati + 1 non applicabile |
| Progettazione Reel 30 s | OK | 12 scene, 750 frame |
| Produzione Reel 30 s | OK | 253,4 s, QC 17 superati + 1 non applicabile |
| Anteprima completa 30 s | OK | 540×960, 25 fps, 25,2 s, 11 clip in cache |
| Anteprima singola scena | OK | 540×960, cache hit verificato |
| QC loudness reale | OK | −14,5 LUFS (20 s), −14,4 LUFS (30 s), TP entro −1 dBTP |
| Conteggio frame esatto | OK | 500/500 e 750/750 con `nb_read_frames` |
| Decodifica completa | OK | zero pacchetti corrotti |
| Logo e payoff in chiusura | OK | frame 29 s verificato a vista |
| Ripristino backup | OK | mappatura cartelle, percorsi ostili rifiutati, backup di sicurezza |
| Blocco regia su `motion_speed` | OK | rifiutato con motivazione |

## 4. Ispezione visiva

Frame estratti dal Reel 30 s (1 s, 8 s, 15 s, 22 s, 29 s):

- immagini autentiche del KORA (pergolato, tavoli, aperitivo al tramonto);
- sottotitoli leggibili nell'area sicura («Il pergolato dosa la luce invece di nasconderla.»);
- outro: logo originale KORA Beach Club su verde profondo, «The Art of Being» intatto,
  «POLICORO · MEDITERRANEAN SEA»;
- nessun frame nero anomalo (controllo `blackdetect` superato).

## 5. Prove non eseguite

Vedi `docs/KORA_3.6_FUNZIONI_NON_COLLAUDATE.md`: modelli OpenCode reali, TTS reale,
generazione C/D/E, vision. Nessuna di queste è presentata come funzionante.

## 6. Difetti trovati e corretti durante il collaudo

1. **Anteprima con clip video.** L'anteprima completa andava in errore quando una
   scena usava una clip video del Vault: il generatore di miniature apriva il file
   con Pillow come se fosse un'immagine (`UnidentifiedImageError`). Corretto
   estraendo il primo frame con FFmpeg. L'anteprima 30 s è stata rigenerata dopo
   la correzione.
2. **Fallback deterministico ingiustificato.** Se lo snapshot dei motori esisteva
   ma non conteneva modelli utilizzabili, KORA scendeva al progetto deterministico
   dichiarando «nessun motore attivo», che era falso. `fast_entry()` ora fa un probe
   reale (con cache) prima di arrendersi. Aggiunto test dedicato
   (`test_fast_entry_probes_when_snapshot_has_no_usable_model`).
3. **Test instabile (non un difetto del prodotto).** `test_api_bootstrap_and_project_reel`
   impostava lo snapshot **prima** del bootstrap; la prima scrittura delle
   impostazioni Essential azzera volutamente lo snapshot, quindi il test a volte
   partiva senza motore. Lo snapshot ora viene impostato dopo il bootstrap, come
   avviene nell'uso reale. La causa è documentata, non mascherata.
