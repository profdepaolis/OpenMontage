# KORA 3.6 — Benchmark del motore creativo

Data: 22 novembre 2026 · build 3.6.0

## Regola

Il benchmark si esegue **solo su richiesta**, dal pannello
Impostazioni → Modelli & benchmark, oppure via `POST /api/ai-models/benchmark`.
Nessuna esecuzione periodica, nessun consumo in background.

- **Test rapido**: `Rispondi soltanto con OK` — misura solo raggiungibilità e latenza.
- **Test creativo**: richiesta KORA reale in JSON compatto (4 temi) — verifica
  struttura, non solo presenza di testo.

Ogni esito registra: data, modello, provider, versione CLI (se nota), tipo di test,
latenza, esito, causa dell'errore, numero di temi validi.

## Risultati in questo ambiente

| Modello | Test | Esito | Latenza |
|---|---|---|---|
| `opencode/hy4-flash-free` | rapido | **non eseguito** | — |
| `opencode/mimo-v2.6-flash-free` | rapido/creativo | **non eseguito** | — |
| `opencode/nemotron-3.5-lightning-free` | rapido | **non eseguito** | — |

Motivo: `opencode` non è installato in questo sandbox e non esiste accesso HTTP
esterno. Nessun tempo viene inventato.

## Valori di riferimento dichiarati dall'utente (non riprodotti qui)

| Modello | Everyday (min 3,0 s / creativo 20,7 s) | Premium (15,8 s / 78,4 s) |
|---|---|---|
| `opencode/hy4-flash-free` | riferimento | — |
| `opencode/mimo-v2.6-flash-free` | — | timeout consigliato 150 s |
| `opencode/nemotron-3.5-lightning-free` | alternativa (2,6 s / 28,4 s) | — |

Questi numeri restano **riferimenti esterni**: vanno riconfermati con un test reale
sulla macchina dell'utente prima di essere usati per scelte automatiche.

## Come leggere il risultato

- badge verde = risposta reale + latenza misurata in questa installazione;
- badge rosso = test eseguito e non superato (con causa classificata);
- «non testato» = nessuna prova: non è un successo;
- suffisso `-free` = dichiarazione del provider, **non** verifica di gratuità.

## Comandi utili

```bash
curl -s -X POST http://127.0.0.1:8020/api/ai-models/benchmark \
  -H 'Content-Type: application/json' -d '{"kind":"quick"}'

curl -s "http://127.0.0.1:8020/api/ai-models/tests?limit=10"
```
