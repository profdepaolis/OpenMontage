# KORA 3.6 — Funzioni non collaudate

Documento di onestà tecnica. Se una funzione non è qui elencata come collaudata,
non è stata provata con un provider reale in questa build.

## Elenco

| Funzione | Stato | Perché | Cosa serve |
|---|---|---|---|
| Elenco modelli `opencode models` | non collaudato | CLI assente nel sandbox | OpenCode installato |
| Selezione modello Everyday/Premium | non collaudata su provider | CLI assente | test reale dopo la scelta |
| Test rapido e creativo su OpenCode | non collaudato | CLI assente | CLI + autenticazione |
| Errore EOL reale | non collaudato | solo firma del messaggio nei test | un modello dismesso da interrogare |
| Generazione immagine→video (C) | non collaudata | provider a consumo, nessuna autorizzazione | credenziale + autorizzazione spesa |
| Generazione video da testo (D/E) | non collaudata | idem | idem |
| TTS ElevenLabs | non collaudato | nessuna chiave/voce in ambiente | chiave API + voce autorizzata |
| TTS OpenAI | non collaudato | nessuna chiave | chiave API |
| TTS Piper locale | non collaudato | modelli voce non installati | `piper` + voce italiana |
| TTS Edge | non collaudato | rete esterna bloccata nel sandbox | rete attiva sul Mac dell'utente |
| Vision `nemotron-3.5-super-vl-free` | non collaudata | CLI assente | test reale su immagine prima dell'uso |
| Costi dei provider | non verificabili | nessun listino interrogabile qui | dichiarazione del provider |

## Collaudato davvero in questa build

- errori del provider riconosciuti dal testo (17 classi, incluse EOL/rete/vuoto);
- fail-fast: nessun timeout artificiale quando il provider risponde con errore;
- cache e ricerca modelli (con CLI simulato nei test);
- persistenza configurazione ruoli e timeout, con limiti 10–600 s;
- ideazione: budget letto dalla configurazione (45 s di default, modificabile);
- stati QC a quattro valori, inclusi «Non applicabile» senza audio o senza voce;
- loudness reale del master (−14 LUFS, true peak ≤ −1 dBTP);
- manifest vocale e chiave di cache per segmento;
- blocchi di regia comprensivi di `motion_speed`;
- ripristino backup mappato, sicuro e reversibile;
- rendering 20 s (500 frame) e 30 s (750 frame) con QC reale;
- anteprima scena 540×960 con cache.

## Regola di lettura

Una riga «non collaudato» non è un difetto nascosto: è un'informazione. KORA non
dichiara operativo un provider che non ha prodotto un file reale.
