# KORA STUDIO Essential — 3.3

**Stabilità della produzione e qualità cinematografica.**
Stato reale al 21 novembre 2026. Nessuna funzione dichiarata "funzionante" senza una verifica eseguibile.

---

## 1. Cosa è stato corretto in questa versione

| # | Area | Prima | Adesso | Verifica |
|---|------|-------|--------|----------|
| 1 | Brand Guardian | `"spa" in testo` → bloccava "spazio/spazi/spazioso" | confini lessicali + normalizzazione Unicode + plurali ("piscine", "terrazze"); il messaggio cita il contesto | test `test_spa_false_positives_are_not_violations`, `test_real_undocumented_services_are_detected` |
| 2 | Fonte unica del testo | `spoken` e `subtitle` calcolati separatamente | una sola stringa per scena: voce, sottotitoli e trascrizione derivano da lì | `test_subtitles_come_from_the_same_text_as_the_voice` |
| 3 | Outro KORA | `renumber()` riordinava per numero → scena aggiunta poteva finire dopo il logo | ordine della lista conservato, una sola chiusura, sempre ultima; eliminazione dell'outro bloccata con motivazione | `test_added_scene_stays_before_the_outro`, `test_outro_cannot_be_deleted_by_accident`, `test_removing_a_middle_scene_keeps_the_logo_last` |
| 4 | Durata vincolante | `recommended_duration_sec` dell'AI batteva la scelta utente | la durata richiesta vince sempre; `duration_locked=false` è l'unica via per accettare il consiglio AI | `test_user_duration_wins_over_ai_recommendation`, `test_duration_free_mode_is_explicit`, `test_frame_math_is_exact_for_required_durations` (375/500/750 frame) |
| 5 | Cache audio | `invalidate_preview()` cancellava il manifest ma `render_preview()` riusava `audio_mix.wav` anche con testi nuovi | impronta dei contenuti (`audio_mix.json`) + controllo data per i progetti vecchi; audio non coerente ⇒ anteprima **muta e dichiarata**, mai audio vecchio con sottotitoli nuovi | `test_audio_is_invalidated_when_text_changes`, `test_audio_without_signature_is_not_considered_good` |
| 6 | Scene editor | "Suggerisci testo" salvava subito; il ramo `guardrail_locale` dichiarava applicato ciò che non salvava | proposta separata (`applied:false`) con **Applica / Annulla**; endpoint `text/apply`; testi manuali validati; parsing JSON strutturato | `test_text_suggestion_is_only_a_proposal`, `test_apply_text_saves_and_declares_source`, `test_api_suggestion_then_apply`, `test_manual_text_is_validated` |
| 7 | Gemini | chiave salvata prima della verifica; `declared_free_quota` imposto d'ufficio; messaggi "nessun addebito possibile" | verifica prima del salvataggio con ripristino della chiave precedente; nessuna quota dichiarata automaticamente; `billing: non_verificabile` | `test_gemini_invalid_key_does_not_overwrite_previous`, `test_gemini_connect_does_not_declare_free_quota` |
| 8 | Preflight | solo diagnostica a posteriori | controlli bloccanti/non bloccanti separati per **anteprima** e **render finale**, controlli narrativi (brand, sottotitoli, durata, outro, logo, audio, voce) prima del rendering; `POST /produce` risponde 409 con l'elenco e permette `force` | `test_readiness_separates_preview_and_render` |
| 9 | Errori | `500` muto | messaggio con causa, fase, codice di uscita FFmpeg, scena e risorsa; log in `~/.kora/logs/errors.log`; `GET /api/system/diagnostics` | `test_preview_failure_explains_the_reason` |
| 10 | Import media | `shutil.copy2` su se stesso (`SameFileError`) poteva bloccare l'avvio | fallback corretto + seeding resiliente (l'app parte, l'errore è loggato) | avvio verificato senza FFmpeg |

## 2. Errore HTTP 500 sull'anteprima — stato reale

**Non è possibile dichiararlo risolto senza il log della tua macchina**: il sandbox non ha FFmpeg, quindi non posso riprodurre il tuo rendering.

Quello che è stato fatto:

- l'endpoint non restituisce più un 500 muto: ora risponde con `RuntimeError: … — FFmpeg non è raggiungibile dal processo KORA: verifica con which ffmpeg e riavvia` (verificato: `pytest test_v28…test_preview_api_flow_and_vision_endpoints` → 500 con dettaglio esplicito);
- ogni eccezione registra **fase, tipo, codice di uscita FFmpeg, scena, risorsa, progetto** in `~/.kora/logs/errors.log`, senza credenziali;
- `GET /api/system/diagnostics` riassume FFmpeg, FFprobe, font, motore e ultimi errori;
- il pannello anteprima, in caso di errore, mostra i controlli di preflight mancanti.

**Per la diagnosi definitiva servono questi due comandi sulla tua macchina:**

```bash
curl -s http://127.0.0.1:8020/api/system/diagnostics
tail -40 ~/.kora/logs/errors.log
```

Con l'output si individua la fase esatta (scene clip, filtergraph overlay, concat, audio, permessi).

## 3. Collaudo

- **Nuovo**: `tests/kora_studio/test_v33_production_stability.py` → 20 test, tutti verdi (logica reale: brand, outro, durata, audio, proposte, Gemini, prontezza).
- Suite `tests/kora_studio`: **137 passed, 4 skipped, 9 failed**.
- I 9 fallimenti sono **tutti** `RuntimeError: FFmpeg non disponibile nel sistema` (questo sandbox non ha il binario): pipeline E2E, export video, preview manifest, revisione scena, two-voice mix, provider locali.
- Test aggiornati al nuovo contratto: `test_v30_essential` (durata utente), `test_v31…` (niente quota gratuita automatica), `test_v32…` (proposta ≠ applicazione).

## 4. Cosa non è stato possibile eseguire (dichiarato)

- **Nessun rendering reale**: manca FFmpeg nel sandbox. Quindi i due Reel dimostrativi (A e B) **non sono stati prodotti**, e non posso dichiarare verificati fluidità, sincronizzazione e resa finale.
- **Nessuna voce Brando/Tiziana**: nel sandbox non ci sono i modelli vocali autorizzati né Edge TTS funzionante; non ho sostituito voci.
- **Nessun motore AI reale**: Gemini/Codex/OpenCode non sono autenticati qui; i percorsi sono testati con adapter simulati (e dichiarati come tali nei test).
- **Progetto `prj_d74fd947f` / job `job_d9f40cef33`**: non presenti nel sandbox. Il recupero va fatto sulla tua macchina: con questa versione i testi si correggono scena per scena senza rigenerare tutto.

## 5. Cosa fare adesso (ordine consigliato)

1. Installare la patch e riavviare.
2. `curl -s http://127.0.0.1:8020/api/system/diagnostics` → se `ffmpeg.ok=false`, `brew install ffmpeg` e riavvio.
3. Aprire il progetto "La quiete del mare d'inverno" → **Produzione scene**: correggere i testi delle scene 1, 3, 5 (ora un solo testo per voce e sottotitoli) → **Genera anteprima**.
4. Mandarmi l'output dei due comandi del punto 2 se l'errore persiste: senza quello ogni ulteriore correzione sarebbe una supposizione.

## 6. Limiti noti e residui

- La ripresa di un job fallito riparte dall'inizio della pipeline: il riuso dei segmenti validi è garantito dalla cache delle clip e dal nuovo controllo audio, non da un checkpoint per fase.
- La trascrizione effettiva dell'audio (ASR) non è verificata: il confronto è testo ↔ sottotitoli. Dichiararlo è parte del requisito 6.
- Il budget interno non sostituisce la fatturazione Google: la pagina motore lo scrive esplicitamente.
- L'interfaccia resta a quattro sezioni (Studio, Archivio, Progetti, Impostazioni); la diagnostica vive nelle impostazioni avanzate.

---

# Aggiornamento 3.4 — «JSON non valido (JSONDecodeError)»

## Causa

Gemini 2.5 Flash risponde quasi sempre con JSON **non stretto**: recinto ```json```, una frase
prima o dopo, virgole finali, oppure risposta **troncata** sul limite di token. Il parser
precedente prendeva dal primo `{` all'ultimo `}` e chiamava `json.loads`: in tutti questi casi
falliva con `JSONDecodeError` e il progetto tornava deterministico, mostrando il messaggio tecnico.

## Correzione

- nuovo modulo `kora_studio/ai_engine/json_extract.py`: rimozione dei recinti, scansione
  bilanciata con rispetto delle stringhe, chiusura automatica delle risposte troncate,
  riparazione di virgole finali / virgolette tipografiche / a capo non escapati, estrazione
  del primo oggetto valido, verifica delle chiavi obbligatorie;
- `reel_designer`: se il JSON resta illeggibile si fa **un solo** ritentativo chiedendo
  esplicitamente un JSON compatto; solo dopo si usa la direzione locale, con messaggio
  comprensibile («Il motore AI ha risposto in un formato non leggibile…») e dettaglio tecnico
  nei `timings_ms` (`json_error`, `json_repair_error`);
- `theme_ideation`: stesso parser e messaggio leggibile;
- prompt aggiornato: JSON compatto su una riga, nessun blocco di codice.

## Verifiche

`tests/kora_studio/test_v34_json_recovery.py` → **11 passed**: recinti, prosa, virgole finali,
risposta troncata, virgolette tipografiche, a capo, JSON assente, chiavi obbligatorie,
design reale con JSON incapsulato, riparazione locale senza seconda chiamata, un solo
ritentativo poi fallback dichiarato, riparazione riuscita che mantiene il design del motore.

Suite `tests/kora_studio`: **148 passed, 4 skipped, 9 failed** (i 9 sono sempre e solo
`FFmpeg non disponibile`).

## Nota per la produzione in corso

Il progetto aperto è stato progettato quando il JSON è caduto: è quindi la **direzione locale**.
Con questa patch, premendo «Riprova» sulla proposta di tema, il progetto viene riprogettato da
Gemini (ora leggibile). L'anteprima intanto è valida: 6 scene, 500 frame, 20,00 s, muta e
dichiarata tale finché non si genera il mix vocale.
