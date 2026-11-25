# KORA STUDIO LIGHT 3.6.8 — Rapporto di collaudo (QA)

Aggiornamento correttivo della 3.6.7. Base: `KORA_Studio_Light_3.6.7_installer.zip`.
Ambiente di collaudo: Linux (sandbox di sviluppo), Python 3.11, FFmpeg del `.venv` di KORA,
frontend compilato. **Motore ASR assente** in questo ambiente (`huggingface.co` irraggiungibile).

Questo rapporto dichiara solo ciò che è stato eseguito. Ciò che non è stato eseguito è in §6.

---

## 1. Consegna

| Artefatto | Dove |
|---|---|
| Installer 3.6.8 | ZIP su Drive (link e md5 nella consegna) |
| Aggiornatore | `aggiorna_kora_3.6.8.sh.txt` su Drive (comando unico nella consegna) |
| Sorgenti | repository, branch `arena/36db4987-openmontage` |
| Frontend compilato | `kora_studio/frontend/out/` (dentro lo ZIP) |
| Suite di test | `tests/kora_studio/` (dentro lo ZIP) — 3 file di sincronizzazione + suite completa |
| Test di regressione | `tests/kora_studio/test_v368_sync_regression.py` (18 test) |
| Report tecnico | `docs/KORA_3.6.8_SINCRONIZZAZIONE_REGRESSIONI.md` |
| Reel di verifica | `KORA_3.6.8_reel_A_…mp4` (21,08 s) e `KORA_3.6.8_reel_B_…mp4` (22,05 s) |
| Transcript / SRT / VTT / timeline | `docs/accettazione_3.6.8/` |
| Report di sincronizzazione | `docs/accettazione_3.6.8/report_accettazione_3.6.8.json` + `./diagnostica_sync.sh` |

Aggiornamento (backup automatico, `kora_data/` mai toccata, rollback automatico):

```bash
curl -fsSL "https://drive.google.com/uc?export=download&id=<ID_AGGIORNATORE>" -o /tmp/aggiorna_kora.sh \
  && bash /tmp/aggiorna_kora.sh
```

---

## 2. Difetti corretti (dettaglio in `docs/KORA_3.6.8_SINCRONIZZAZIONE_REGRESSIONI.md`)

1. **Modalità copione**: ora chiama davvero l'ASR, allinea il copione ai tempi riconosciuti,
   elenca le parole non riconosciute e salva la provenienza. Senza ASR dichiara
   `alignment="estimated"` e non chiama «allineamento» una distribuzione proporzionale.
   `apply_uploaded_voiceover()` non spegne più l'ASR quando c'è un copione.
2. **Più frasi nella stessa scena**: `caption_timings_from_transcript()` usa tutti i segmenti
   della scena, in ordine; ogni parola mantiene il tempo del proprio segmento; supporta pause,
   scene senza parlato e frasi divise su due scene.
3. **Stato `verified`**: `verified=True` solo con parole provenienti dall'ASR;
   `words_from_asr()` conserva la provenienza (`asr` / `forced_aligned` / `estimated` / `manual`);
   la timeline espone `verification.timing_status`, `verification.text_status` e i conteggi.
4. **`verifica_test.sh --veloce`**: esegue solo i test di sincronizzazione.
5. **Produzione**: se l'utente ha chiesto ASR/copione, la pipeline non ripiega più sul testo
   creativo (o usa il copione salvato, o resta senza parole, dichiarandolo nel log).
6. **QC**: nuovo controllo bloccante «Nessuna parola del copione spacciata per riconosciuta».

---

## 3. Suite completa — esito

```
297 passed, 3 skipped in 977.36 s (0:16:17)
```

Test di sincronizzazione eseguiti in isolamento:

| File | Esito |
|---|---|
| `test_v366_audio_timeline.py` | 16 passed |
| `test_v367_speech_sync.py` | 16 passed |
| `test_v368_sync_regression.py` | 18 passed |
| **totale `./verifica_test.sh --veloce`** | **50 passed** |

---

## 4. Collaudo audiovisivo (MP3 italiani reali, motore ASR assente)

Script riproducibile `tools/accettazione_368.py`; artefatti in `docs/accettazione_3.6.8/`.

| Percorso | Audio | Modalità | Stato dichiarato | Reel |
|---|---|---|---|---|
| A · MP3 + copione | `voce_brando.mp3` 21,08 s | `script` | `estimated` · 22 parole · 10 cue · `verified=false` · 0 discordanze | 21,08 s · 527 frame · 1080×1920 · H.264 + AAC |
| B · MP3 senza copione | `voce_tiziana.mp3` 22,05 s | `asr` | `none` · 0 parole · 0 cue (**nessun testo inventato**) | 22,05 s · 551 frame · 1080×1920 · H.264 + AAC |

Verifiche automatiche: audio completo nel file finale, nessuna cue oltre la fine della voce,
SRT esportato coincidente con le cue della timeline (14 e 0 righe), frame estratti a 6 s e 18 s
con sottotitoli bruciati e word-highlight HyperFrames.

**Cosa questo collaudo NON dimostra:** la corrispondenza fonetica parola↔audio. Senza modello
Whisper non è possibile trascrivere l'MP3: i tempi del percorso A sono stimati dal copione
dentro le pause misurate, quelli del percorso B non esistono. Il collaudo fonetico richiede
`./installa_asr.sh` sul Mac (§6).

---

## 5. Non regressioni verificate

* Suite completa verde al netto dei test opzionali saltati (Remotion/Chromium assenti).
* Nessuna modifica a Brand Brain, OpenCode/HY4/MiMo/Nemotron, Media Vault, Creative Studio,
  Script Director, Storyboard, Scene Editor, Voice Studio, FFmpeg, HyperFrames, Remotion,
  AI Media Lab, cache, backup, archivio progetti, logo e asset.
* Nessuna funzione o voce di menu rimossa; unica aggiunta visiva: l'elenco delle discordanze
  nel pannello «Trascrizione & Sincronizzazione».
* `kora_data/` non viene toccata né dall'installer né dall'aggiornatore (verificato nel flusso).

---

## 6. Non collaudato (dichiarato, non simulato)

1. **Trascrizione ASR reale** di un MP3 italiano: `huggingface.co` non raggiungibile in sandbox,
   nessun modello scaricabile. Il percorso ASR è testato con risultati forniti come input e con
   un motore sostituito esplicitamente nei test, non con una trascrizione vera.
2. **Installazione su macOS** (Intel e Apple Silicon) e livelli `rapido`/`bilanciato`/`accurato`.
3. **Allineatore acustico** (MFA/Gentle): l'allineamento è testuale sui tempi ASR.
4. **Diarizzazione** e editor multi-traccia.
5. **Click reali nel browser** sui nuovi messaggi di discordanza.
6. **Verifica a campione delle parole pronunciate** nel reel finale (impossibile senza ASR).

Procedura di attivazione sul Mac:

```bash
cd ~/Documents/"Kora Studio Light"
./installa_asr.sh --verifica
./installa_asr.sh --livello bilanciato
./diagnostica_sync.sh
```

Poi, nello Storyboard → «Trascrizione & Sincronizzazione»: «Trascrivi automaticamente» (MP3
senza testo) o «Allinea un copione» (MP3 con testo). La sincronizzazione potrà dirsi completa
solo quando, con il motore installato, tutte le frasi di un MP3 reale risulteranno associate
alle scene e ai sottotitoli renderizzati, con `alignment="asr"` o `forced_aligned` e
discordanze pari a zero.
