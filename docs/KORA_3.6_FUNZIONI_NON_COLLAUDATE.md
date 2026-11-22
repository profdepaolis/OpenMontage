# KORA 3.6.1 — Funzioni non collaudate

Documento di onestà tecnica. Se una funzione non è qui elencata come collaudata,
non è stata provata con un provider reale in questa build.

## Aggiornamento 3.6.1

Con il ripristino della pipeline AI le voci «non collaudate» restano tali: i test
3.6.1 usano un **doppio di prova** del CLI `opencode` (eseguibile finto) che
percorre lo stesso codice di produzione, ma **non** dimostrano che HY4 Flash, MiMo
Flash o Nemotron Lightning rispondano davvero. La verifica reale è lo script
`diagnostica_ai.sh` sul Mac dell'utente.

| Funzione | Stato dopo la 3.6.1 |
|---|---|
| Migrazione legacy DeepSeek → HY4 | collaudata (test + prova su DB temporaneo) |
| Modello trasmesso esplicitamente (Everyday/Premium) | collaudata con doppio di prova |
| Timeout ideazione 45 s, risposta a 21 s | collaudata con doppio di prova |
| Politica «Chiedi conferma» con quattro scelte | collaudata (API + interfaccia compilata) |
| Gemini non attiva il motore | collaudata |
| Chiamata reale a `opencode/hy4-flash-free` | **non collaudata** (CLI assente) |
| Latenza reale su EOL/Mac | **non collaudata** (benchmark fornito dall'utente) |

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
