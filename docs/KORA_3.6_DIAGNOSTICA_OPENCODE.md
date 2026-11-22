# KORA 3.6 — Diagnostica OpenCode

Data: 22 novembre 2026 · ambiente di collaudo della build 3.6.0

## Esito in una riga

**OpenCode non è installato in questo ambiente**: la catena dei modelli è
verificata solo nella struttura (elenco, cache, selezione, test, gestione errori).
Nessun risultato di inferenza viene dichiarato.

## Rilevazione automatica (reale, eseguita)

```text
GET /api/ai-models/diagnostics
{
  "installed": false,
  "binary": null,
  "version": null,
  "detail": "opencode non trovato nel PATH",
  "models_cached": 0,
  "models_error": "opencode non trovato nel PATH",
  "selected": {
    "everyday": "opencode/hy4-flash-free",
    "premium": "opencode/mimo-v2.6-flash-free",
    "fallback": "opencode/nemotron-3.5-lightning-free"
  }
}
```

La risposta è quella reale del server 3.6 avviato in locale: KORA dichiara
l'assenza invece di mostrare un elenco vuoto come se fosse un successo.

## Comandi che KORA usa (compatibili con il CLI reale)

| Funzione | Comando | Note |
|---|---|---|
| Rilevamento | `opencode --version` | esegue il binario, non guarda solo il PATH |
| Elenco modelli | `opencode models` | **senza** argomento provider (non supportato) |
| Inferenza | `opencode run --model <id> "<prompt>"` | prompt come argomento, mai shell |
| Allegati | `--file <path>` (o `-f` se la versione è più vecchia) | scoperto una volta e messo in cache |
| Autenticazione | `opencode auth list --format json`, poi `auth list`, `auth status` | solo lettura |

## Gestione degli errori (verificata su testo, non su provider reale)

| Caso | Riconoscimento | Esito KORA |
|---|---|---|
| Modello EOL («has reached its end of life», «no longer available», «deprecated») | `provider_failure()` anche con exit 0 | `EngineModelRetired`, immediato, candidato successivo se autorizzato |
| Modello inesistente | exit ≠ 0 o testo | `EngineModelUnavailable` |
| Quota esaurita | testo/`429`/`quota` | `EngineQuotaExhausted` |
| Rate limit | `429`, «too many requests» | `EngineRateLimited` (ritenta con attesa breve) |
| Autenticazione | `401/403`, «unauthorized», «invalid api key» | `EngineAuthError` |
| Rete | DNS/SSL/connessione | `EngineNetworkError` |
| Risposta vuota | nessun testo utile | `EngineEmptyResponse` |
| Timeout reale | processo ancora vivo alla scadenza | `EngineTimeout` |

Tutte le classi sono testate in `tests/kora_studio/test_v36_model_manager.py`.
La firma EOL usata nei test è quella reale riportata dall'utente:

```text
The model 'nvidia/deepseek-ai/deepseek-v4.1-flash' has reached its end of life
on 2026-11-16T08:00:00Z and is no longer available.
```

## Cosa manca per chiudere il collaudo

1. `opencode` installato e autenticato su un Mac reale;
2. esecuzione di `Aggiorna elenco` → verifica che compaiano i modelli;
3. test rapido su `opencode/hy4-flash-free` (atteso OK in pochi secondi);
4. test rapido sul modello dismesso: KORA deve rifiutare in ~1–2 s con messaggio
   «modello dismesso», **non** attendere il timeout;
5. test creativo su `opencode/mimo-v2.6-flash-free` (4 temi JSON);
6. registrazione di data, versione CLI, latenza e configurazione nel benchmark.

Senza questi passi la filiera resta **non collaudata**: è la posizione corretta.
