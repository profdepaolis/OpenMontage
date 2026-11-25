# Collaudo 3.6.9 — sottotitoli editoriali

Caso riprodotto: voce che legge una frase completa, sottotitoli brevi scritti a mano
(sintesi creativa). È la situazione segnalata dall'utente su «L'aperitivo senza fretta».

- `report_3.6.9.json` — esito delle due produzioni reali (macchina, timestamp, esiti).
- `prj_6de272f4a3_reel_final.mp4` — Reel prodotto **dopo** la dichiarazione editoriale
  (1080×1920, H.264 + AAC, 21,08 s).
- `prj_6de272f4a3_sottotitoli.srt` / `.vtt` / `_cues.json` — sottotitoli consegnati:
  nelle scene 1-3 si legge il testo dell'utente («Il pomeriggio rallenta.»,
  «Ombre lunghe.», «Un bicchiere, il silenzio.»), non la trascrizione della voce.
- `prj_6de272f4a3_timeline.json`, `_transcript.json` — timeline canonica e trascrizione
  dichiarata (`alignment = none`, nessun motore ASR su questa macchina).
- `prj_6de272f4a3_report_tecnico.json` — report QC completo dell'esportazione.

## Esito in sintesi

| passo | risultato |
|---|---|
| 1 · produzione con sottotitoli «fedeli» (predefinito) | job **Errore**, messaggio con la sola causa bloccante, i due rimedi e gli avvisi separati |
| 2 · dichiarazione «editoriali sintetici» + nuova produzione | job **Completato**, MP4 prodotto, QC superato con **avviso** e revisione umana |
| sottotitoli dell'utente dopo il ricalcolo | **intatti** |
| controllo «Coerenza tra voiceover e sottotitoli» | `passed=true`, `severity=warning`, `requires_human_review=true` |

Messaggio della produzione bloccata (estratto reale):

> Esportazione bloccata dal Controllo Qualità: Coerenza tra voiceover e sottotitoli:
> Testo e sottotitoli raccontano cose diverse (scena 1: voce «Nel tardo pomeriggio
> d'autunno,» ≠ sottotitoli «Il pomeriggio rallenta.» …). Due rimedi: (1) Storyboard →
> «Allinea sottotitoli al testo» …; (2) se i sottotitoli sono una scelta editoriale,
> dichiarali in Storyboard → Trascrizione & Sincronizzazione → Testo dei sottotitoli:
> «Editoriali sintetici». Poi premi Riprova. — Avvisi (non bloccanti): Trascrizione
> verificata sulle parole pronunciate: Tempi stimati dentro le pause misurate (nessun
> motore ASR): i sottotitoli sono coerenti con il testo, non trascritti dalla voce.

## Come ripetere il collaudo

    rm -rf /tmp/acc369 && mkdir -p /tmp/acc369
    KORA_DATA_DIR=/tmp/acc369/data .venv/bin/python tools/accettazione_369.py --out /tmp/acc369

Lo script non simula nulla: crea il progetto, importa `assets/kora/voices/voce_brando.mp3`,
esegue due volte `run_production_pipeline`, legge job, MP4 e report QC dai file prodotti
e termina con codice 0 solo se tutte le condizioni sono soddisfatte.
