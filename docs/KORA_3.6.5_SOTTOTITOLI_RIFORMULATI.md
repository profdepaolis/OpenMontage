# KORA STUDIO LIGHT 3.6.5 — Sottotitoli riformulati

Data: 2026-11-23 · Branch `arena/36db4987-openmontage`

## Cosa è successo davvero (tentativo v41, job `job_a7dc6fdb8b`)

Dopo la 3.6.4 il messaggio è diventato preciso, e ha mostrato che il mio
criterio era ancora troppo severo. Il progetto «La luce radente del
tramonto» veniva fermato con:

```
Coerenza tra voiceover e sottotitoli: Testo e sottotitoli raccontano cose
diverse (scena 2: voce «La luce di novembre sfiora il pergolato, posa i…»
≠ sottotitoli «La luce sfiora il pergolato»; scena 3: voce «Il sale resta
sull'immobile, il posto è già pro…» ≠ sottotitoli «Un posto, già pronto
per te»).
```

Ma quei due sottotitoli **non raccontano cose diverse**: sono la stessa
frase, riformulata più corta con le parole dello speaker. La 3.6.4
accettava solo prefisso letterale o similarità di caratteri ≥ 0,6;
qui la somiglianza carattere per carattere scende sotto quella soglia
perché l'ordine e le parole funzionali cambiano («Un posto, già pronto
per te» è un riassunto che sposta l'inciso).

## La correzione: contano le parole portanti

`kora_studio/agents/quality_controller.py` — nuovo confronto lessicale
oltre a `difflib`:

```python
def _content_words(text)      # parole senza articoli, preposizioni, congiunzioni
def _word_coverage(voce, sub) # quota di parole portanti del sottotitolo già dette
```

Il sottotitolo è **sintetico** (avviso, `superato`, revisione consigliata)
quando almeno una condizione è vera:

| Condizione | Esempio |
|---|---|
| prefisso letterale in un verso o nell'altro | «La luce sfiora…» ⊂ frase intera |
| similarità caratteri ≥ 0,6 | riformulazione quasi identica |
| **copertura delle parole portanti ≥ 0,75** (novità 3.6.5) | «Un posto, già pronto per te» da «…il posto è già pronto per te» |

Blocca (`severity="error"`) solo se nessuna vale — cioè quando il
sottotitolo parla d'altro: «Il vento sposta le sedie del portico» contro
«Il mare è calmo e limpido stamattina» → copertura 0,0.

Misure sui casi reali:

| Voce | Sottotitoli | Copertura | Esito |
|---|---|---|---|
| La luce di novembre sfiora il pergolato, posa i suoi fili d'ombra. | La luce sfiora il pergolato | 1,00 | avviso |
| Il sale resta sull'immobile, il posto è già pronto per te. | Un posto, già pronto per te | 1,00 | avviso |
| Il mare è calmo e limpido stamattina. | Aperitivo al tramonto con musica dal vivo | 0,00 | blocco |
| Il sale resta sull'immobile, il posto è già pronto per te. | Il vento porta salsedine sui tavoli | 0,00 | blocco |

## Secondo difetto corretto: il messaggio si ripeteva

`kora_studio/agents/export_manager.py` — il testo di blocco concatenava i
dettagli e poi la diagnosi di ripiego, che ripete gli stessi dettagli:

```
… Riprova. Diagnosi: Correggere i controlli falliti e rieseguire il
montaggio: Coerenza tra voiceover e sottotitoli: Testo e sottotitoli
raccontano cose diverse (scena 2: …)
```

Ora la costruzione è isolata in `_qc_block_message(report, diagnosis)`:

- la diagnosi di ripiego (quella che inizia con «Correggere i controlli
  falliti») viene omessa, perché non aggiunge nulla ai dettagli già scritti;
- una diagnosi vera del motore AI resta, ma solo se non è già contenuta
  nel testo.

## Collaudo

| Verifica | Esito |
|---|---|
| `tests/kora_studio/test_v364_caption_coherence.py` (8 test: sintetico, riformulato su due scene reali, divergente, messaggio non duplicato, endpoint, idempotenza, sottoinsieme, voce intatta) | **8 passed** |
| Copertura lessicale misurata a mano sui quattro casi della tabella | coerente |
| Suite completa `tests/kora_studio` | **247 passed, 3 skipped in 928 s** |

**Non collaudato:** resa su Mac Intel reale (test su Linux); nessuna
prova con sottotitoli in lingua diversa dall'italiano (le stopword sono
italiane: con testi inglesi la copertura degrada verso il confronto
`difflib`, quindi si resta prudenti, non permissivi).

## Riprendere il progetto

Dopo l'aggiornamento basta **Production → Riprova**: il progetto
`prj_5f62ca8bf7` ha già la cache delle scene, si rifanno solo montaggio
(~65 s) e controllo qualità.

In alternativa, senza aggiornare, si può già sbloccare con
**Storyboard → «Allinea sottotitoli»** (i sottotitoli diventano la frase
intera, la voce non si tocca).

## File toccati

- `kora_studio/agents/quality_controller.py` — `_content_words`, `_word_coverage`, soglia 0,75
- `kora_studio/agents/export_manager.py` — `_qc_block_message`
- `tests/kora_studio/test_v364_caption_coherence.py` — 3 test in più
- `kora_studio/api/app.py` (versione 3.6.5), `setup.sh`, `LEGGIMI.txt`, `INSTALLAZIONE.md`
