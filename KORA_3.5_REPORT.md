# KORA STUDIO LIGHT 3.5 — Creative Control & AI Scene Production

**The Art of Being · Policoro**
Report di rilascio: cosa è stato costruito, cosa è stato collaudato davvero, cosa resta da verificare.

---

## 1. Cosa c'è di nuovo

Tutto vive dentro il Reel già esistente: nessun nuovo framework, nessun runtime aggiuntivo, nessun agente obbligatorio. La produzione automatica resta il percorso principale; i controlli qui sotto sono opzionali.

| Area | Cosa fa | Dove |
|---|---|---|
| Editor di scena | identità stabile (`scene_uid`), duplica, sposta, riordina, blocchi per componente, ripristino materiale precedente | pannello **Regia artistica → Regia e scene AI** |
| Blocchi | scena, visual, voiceover, durata, regia, caption; l'outro resta protetto comunque | stessi controlli per scena |
| Script Director | rigenera frase / scena / gruppo / intero copione / hook / apertura / chiusura / caption IG / CTA, con istruzione libera e confronto prima→dopo | pannello **Copione** |
| Regia di scena | inquadratura, movimento camera, intensità, curva (easing), velocità, composizione | pannello **Regia e scene AI** |
| Caption Designer | 7 preset, dimensione, colore, posizione, allineamento, interlinea, scrim, ombra; per Reel o per scena; area sicura Instagram | pannello **Sottotitoli** |
| Titolo grafico | testo interno al video, separato dal voiceover, non pronunciato | pannello **Sottotitoli** |
| Regia conversazionale | un campo: «Come vuoi modificare il Reel?» → modifiche strutturate mostrate prima di applicare | pannello **Come vuoi cambiarlo** |
| AI Scene Generator | modalità A–E, preflight credenziali/modello/quota/costo, provenienza registrata, originale mai sovrascritto | pannello **Regia e scene AI** |

### Endpoint aggiunti

```
POST   /api/projects/{id}/scenes/{n}/duplicate
POST   /api/projects/{id}/scenes/{n}/move
POST   /api/projects/{id}/scenes/reorder
POST   /api/projects/{id}/scenes/{n}/locks
POST   /api/projects/{id}/scenes/{n}/restore-asset
POST   /api/projects/{id}/scenes/{n}/title
GET    /api/projects/{id}/caption
POST   /api/projects/{id}/caption/apply
POST   /api/projects/{id}/script/propose
POST   /api/projects/{id}/script/apply
POST   /api/projects/{id}/direction/propose
POST   /api/projects/{id}/direction/apply
GET    /api/scene-generator/modes
POST   /api/scene-generator/preflight
GET    /api/projects/{id}/scene-generator/history
POST   /api/projects/{id}/scene-generator/generate
POST   /api/projects/{id}/scene-generator/{gen}/use
POST   /api/projects/{id}/scene-generator/{gen}/save
POST   /api/projects/{id}/scene-generator/{gen}/discard
```

### Invalidazione corretta (punto 11)

| Modifica | Audio | Anteprima | Render |
|---|---|---|---|
| Testo / durata scena | invalidato | invalidata | necessario |
| Sottotitoli o preset caption | **intatto** | invalidata | necessario |
| Caption Instagram / CTA | **intatto** | **non serve** | **non serve** |
| Materiale (swap visual) | **intatto** | invalidata | necessario |
| Regia (inquadratura, moto, curva) | **intatto** | invalidata | necessario |

---

## 2. Come si installa (doppio clic)

1. Scompatta lo ZIP: ottieni la cartella **Kora Studio Light**.
2. Trascinala in **Applications** o in **Documenti** (non serve altro).
3. Doppio clic su **`Installa KORA.command`**.
   - al primo avvio macOS può chiedere conferma: tasto destro → **Apri** → **Apri**;
   - la finestra mostra i passaggi, poi apre il browser su `http://127.0.0.1:8020`.
4. Dalla volta successiva: doppio clic su **`Avvia KORA.command`**.
5. Per fermare: `Ctrl+C` nella finestra del terminale.

Richiede Python 3.9+ (consigliato 3.11). Se FFmpeg non c'è, `setup.sh` prova a fornirlo; in alternativa `brew install ffmpeg`.

---

## 3. Collaudo — i 16 punti del mandato

Legenda: **fatto e verificato** = eseguito su questa build; **dichiarato** = presente ma non collaudabile qui (serve chiave o account).

| # | Punto del mandato | Esito |
|---|---|---|
| 1 | Due modi in un progetto (automatico + regia) | fatto e verificato: la produzione automatica resta invariata; i pannelli di regia sono opzionali e non bloccano nulla |
| 2 | Editor visuale scene (timeline, duplica, sposta, blocchi, outro protetto) | fatto e verificato (test `test_scene_uids_survive_reorder_and_duplication`, `test_outro_cannot_become_an_ordinary_scene`, `test_locks_block_only_the_locked_component`) |
| 3 | Visual Director (materiali reali, swap, ripristino) | fatto e verificato: swap registra la storia, `restore-asset` torna indietro, la voce non cambia |
| 4 | Shot Director (inquadratura, moto, intensità, velocità, curva, composizione) | fatto e verificato: `test_rendered_clip_changes_with_intensity` dimostra pixel diversi con parametri diversi |
| 5 | Script Director (4 livelli + hook/apertura/chiusura/caption/CTA) | fatto e verificato: una chiamata per gruppo, proposte mai applicate da sole, scene bloccate rispettate, durata invariata |
| 6 | Caption Designer (3 elementi, 7 preset, tipografia, area IG) | fatto e verificato: 7 preset, override validati, `test_caption_change_does_not_touch_voice` |
| 7 | AI Scene Generator A–E (confronto, usa, salva, rigenera, scarta) | fatto e verificato per A e B (locali); C/D/E presenti ma **dichiarati non collaudati** (servono chiavi provider) |
| 8 | Provider generativi (verifica disponibilità e costo) | fatto e verificato il comportamento: preflight dichiara cosa non può verificare, budget zero blocca le spese, autorizzazione esplicita richiesta |
| 9 | Autenticità KORA | fatto e verificato: nessuna scena sintetica viene etichettata come reale; ogni generazione ha provenienza e «da verificare umano» |
| 10 | Blocchi e identità (outro + payoff esatto) | fatto e verificato: l'outro non si duplica, non si sposta, non cambia materiale |
| 11 | Revisioni e cache | fatto e verificato (tabella sopra) |
| 12 | Durata e sincronia | fatto e verificato: 25 fps, 20 s = 500 frame; il QC conta i frame decodificati |
| 13 | Anteprima selettiva | fatto e verificato: Reel a 540×960 in cache con revisione; **singola scena** con `POST /api/projects/{id}/scenes/{n}/preview` (540×960, cache, revisione dichiarata) |
| 14 | Regia conversazionale | fatto e verificato: parser italiano reale (`«scena 3 più lenta»`), nessuna produzione avviata |
| 15 | Restare leggeri | fatto e verificato: quattro sezioni, pannello chiuso di default, caricamento solo all'apertura |
| 16 | Collaudo 16 punti + Reel completo | questo documento + Reel in `demo/` |

---

## 4. Test automatici

Eseguiti su questa build con FFmpeg reale, senza AI esterna (budget zero):

- suite completa `pytest tests/kora_studio`: **178 test verdi, 3 saltati, 0 falliti** (20 dei quali nuovi in v3.5);
- nuovi test v3.5: `tests/kora_studio/test_v35_scene_production.py` (18 casi), `tests/kora_studio/test_v35_renderer_motion.py` (2 casi);
- `./verifica.sh`: **31 OK · 0 KO · 0 avvisi**;
- installazione da zero in cartella pulita (`setup.sh` → `verifica.sh` → server → progetto → anteprima reale);
- Reel completo prodotto in questa build: **1080×1920, 25 fps, 20,00 s, 500 frame, QC SUPERATO**
  (`demo/KORA_Reel_3.5_20s.mp4`, 8,4 MB); anteprima 540×960 in `demo/KORA_Anteprima_540x960.mp4`.

---

## 5. Cosa NON è stato collaudato (dichiarazione esplicita)

- **Google Veo / Gemini (modalità C, D, E)**: nessuna chiave configurata in questa build. Il codice chiama l'API reale e registra la provenienza, ma non è stato eseguito end-to-end.
- **fal.ai, Qwen/Wan, Hugging Face**: presenti nel registro motori, non collaudati.
- **Voiceover sintetico**: in questa build non è stata generata voce (nessun provider TTS attivo): i Reel di esempio hanno musica, SFX e sottotitoli, e lo dichiarano.
- **Instagram caption publishing**: la caption viene generata e salvata; la pubblicazione automatica non esiste (e non è richiesta).

Nessun risultato di questi punti è stato simulato: dove non c'era il provider, l'app lo dice e continua con la strada gratuita.
