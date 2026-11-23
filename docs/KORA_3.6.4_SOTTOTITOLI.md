# KORA STUDIO LIGHT 3.6.4 — Sottotitoli allineati

Data: 2026-11-23 · Branch `arena/36db4987-openmontage`

## Il problema reale

Progetto **«La luce radente del tramonto»** (`prj_5f62ca8bf7`, job
`job_job_85d13cda3f`, 6 scene / 612 frame).

Il montaggio è andato a buon fine (71,5 s, `prj_5f62ca8bf7_preview.mp4`),
ma l'esportazione è stata fermata:

```
[KoraDirector] Esportazione bloccata dal Controllo Qualità:
Coerenza tra voiceover e sottotitoli: Possibile discordanza testo/voce
alle scene [2, 3, 5]
```

Non c'era nulla di rotto nel video. Il controllo di qualità confrontava il
testo pronunciato (`spoken_text`) con il sottotitolo (`subtitle_text`)
usando **solo l'uguaglianza esatta** o il rapporto di sottostringa:

```python
if sp != sb and sp not in sb and sb not in sp:   # 3.6.3 e precedenti
    ...  # qualunque differenza → severity="error"
```

Un sottotitolo deliberatamente più corto della frase detta (per stare nei
due secondi della scena) finiva quindi tra gli **errori**, e un errore
attiva `blocks_delivery()`: il regista rifiutava di consegnare un Reel
tecnicamente corretto.

## La correzione

`kora_studio/agents/quality_controller.py` — il controllo ora distingue due
casi, con `difflib.SequenceMatcher`:

| Caso | Regola | Esito |
|---|---|---|
| Allineato | testi identici dopo normalizzazione | nessuna voce |
| **Sintetico** (legittimo) | sottotitolo prefisso del testo (o viceversa) **oppure** similarità ≥ 0,6 | `passed=True`, `severity="warning"`, `status="superato"`, `requires_human_review=True`, dettaglio «Sottotitoli più sintetici del testo alle scene […]» |
| **Divergente** | nessuna delle due condizioni | `passed=False`, `severity="error"` → blocca, ma il dettaglio dice *quali* scene e *cosa* dicono i due testi |

Esempio di messaggio bloccante (nuovo):

```
Testo e sottotitoli raccontano cose diverse (scena 2: voce «…» ≠
sottotitoli «…»; scena 3: …). Apri Storyboard → «Allinea sottotitoli al
testo» per riallineare le scene indicate, poi premi Riprova.
```

`agents/export_manager.py` continua a riportare il motivo del blocco in
italiano, ma ora il motivo è comprensibile e porta a un rimedio.

## Il rimedio in un clic

`kora_studio/agents/scene_editor.py` — nuovo `align_subtitles(project_id,
numbers=None)`: copia `subtitle_text = spoken_text` sulle scene indicate
(tutte se `numbers` è `None`), salta l'outro e le scene vuote, **rispetta il
blocco sottotitoli** (`locks["caption"]`, finiscono in `skipped`), salva con
`_persist_scenes` e invalida solo la preview. **Voce, audio e clip non
vengono rigenerati.**

`kora_studio/api/app.py` — nuovo endpoint:

```
POST /api/projects/{project_id}/captions/align
     corpo opzionale: {"scene_numbers": [2, 3, 5]}
     risposta: {"aligned": [...], "skipped": [...], "total_scenes": 6}
```

Frontend: pulsante **«Allinea sottotitoli»** accanto ad «Aggiorna» nello
Storyboard (`SceneEditor.tsx`, `koraApi.alignCaptions` in `src/lib/api.ts`),
con messaggio di esito in italiano.

## Riprendere il progetto fermo

Il render è già in cache (`scene_cache` presente): dopo l'allineamento si
rieseguono solo montaggio e controllo qualità — la voce non si tocca.

```bash
# 1. allinea i sottotitoli al testo pronunciato
curl -s -X POST http://127.0.0.1:8020/api/projects/prj_5f62ca8bf7/captions/align

# 2. rilancia la produzione (o premi «Riprova» nell'interfaccia)
curl -s -X POST http://127.0.0.1:8020/api/projects/prj_5f62ca8bf7/production/retry
```

In interfaccia: **Storyboard → «Allinea sottotitoli» → Production → Riprova**.

## Collaudo

| Verifica | Esito |
|---|---|
| `tests/kora_studio/test_v364_caption_coherence.py` (sottotitolo sintetico → avviso; testi divergenti → blocco con numeri di scena; endpoint di allineamento; idempotenza; sottoinsieme di scene) | **5 passed** |
| `npx tsc --noEmit` + `npm run build` (interfaccia) | OK |
| Suite completa `tests/kora_studio` | vedi log `/tmp/pytest_364.log` |

**Non collaudato:** resa del montaggio su un Mac Intel reale con questo
pacchetto (i test girano su Linux), e la resa percettiva del preset
`veryfast` rispetto a `medium` (invariata rispetto alla 3.6.3).

## File toccati

- `kora_studio/agents/quality_controller.py` — controllo di coerenza riscritto
- `kora_studio/agents/scene_editor.py` — `align_subtitles()`
- `kora_studio/api/app.py` — `POST /captions/align`, versione 3.6.4
- `kora_studio/frontend/src/components/SceneEditor.tsx`, `src/lib/api.ts`, `out/`
- `tests/kora_studio/test_v364_caption_coherence.py`
- `setup.sh`, `LEGGIMI.txt`, `INSTALLAZIONE.md`
