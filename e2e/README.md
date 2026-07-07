# E2E — Playwright contro gli ambienti deployati

Suite end-to-end che gira contro **produzione Railway** (web-admin, web-merchant, API) con un
merchant dedicato ai test (`e2e-playwright` / "E2E Playwright"), creato automaticamente dallo
spec `02` se non esiste. Non tocca i merchant reali.

## Esecuzione

```bash
cd e2e
cp .env.example .env   # e compila le credenziali
pnpm install
npx playwright install chromium
npx playwright test                 # tutta la suite (seriale, 1 worker)
npx playwright test tests/05-kb-rag.spec.ts   # un solo spec
npx playwright show-report          # report HTML dell'ultima run
```

## Cosa copre

| Spec | Copre |
|---|---|
| `01-admin-login` | Login web-admin (+ caso credenziali errate) |
| `02-merchant-setup` | Creazione merchant + utente `merchant_user` dalla UI admin (idempotente) |
| `03-merchant-login` | Login web-merchant, playground raggiungibile |
| `04-config-responses` | La config bot cambia le risposte: baseline → istruzioni aggiuntive/firma → lingua `en` → cleanup |
| `05-kb-rag` | RAG: 0 chunk senza KB → upload TXT → indicizzazione ARQ → chunk recuperati + risposta fondata → domanda off-topic senza chunk |
| `06-impersonation` | "Entra come merchant" → nuovo tab con token → playground funzionante |

## Scelte di design

- **Asserzioni sulla risposta di rete di `POST /playground/turn`** (`reply_text`,
  `retrieved_chunks`, `model`), non sulle bolle in UI: le bolle arrivano con ritardi di
  digitazione simulati (ADR 0008) e sarebbero flaky.
- **Marker deterministici** (`E2E-QUARZO`, firma `E2E Wave`, prezzo `149`, pacchetto
  "Onda Zaffiro" inventato) per non dipendere dalla creatività dell'LLM. L'asserzione
  sulla lingua usa un conteggio di stopword it/en.
- **Seriale, 1 worker**: i test condividono lo stato del merchant E2E (config, KB).
- Ogni run del playground consuma vere chiamate OpenAI (gpt-5-mini + gpt-5-nano sentiment,
  HyDE/rerank quando la KB non è vuota): ~10 turni per run completa.

## Bug di piattaforma trovati dalla suite (2026-07-06)

1. **HyDE e re-ranking RAG rotti in produzione** — `libs/ai_core/src/ai_core/llm.py:82-83`
   invia `max_tokens` alle chat completions OpenAI, ma i modelli reasoning gpt-5.x lo
   rifiutano (`400 unsupported_parameter`, riprodotto con il modello di produzione
   `gpt-5.4-mini`: serve `max_completion_tokens`). `RAGEngine._hyde` e `_rerank` catturano
   l'eccezione e ripiegano **in silenzio** (log a livello debug) sulla query grezza /
   ordine originale: il RAG di fatto gira senza HyDE né rerank per tutti i tenant.
2. **`rag.min_score` default 0.7 irraggiungibile con la query grezza** — con
   `text-embedding-3-small`, una domanda quasi-verbatim sul contenuto del documento
   scora ~0.667 (misurato in produzione). Con HyDE rotto (bug 1) il retrieval restituisce
   sempre 0 chunk e la domanda finisce in `kb_gaps` anche quando la risposta è in KB.
   La suite usa `rag.min_score=0.55` per il merchant E2E (impostato via API, vedi
   `helpers/api.ts`) per poter testare il loop RAG end-to-end.
3. **`bot.language` non efficace** — il prompt include "Rispondi sempre in lingua en."
   (`conversation_service.py:508`) ma con cliente che scrive in italiano il modello
   risponde comunque in italiano (deterministico su 2 run). Test relativo in `skip`
   finché l'istruzione non viene rafforzata.
4. **`kb_gaps` non deduplica** — `RAGEngine._log_gap` fa `ON CONFLICT DO NOTHING` ma la
   tabella non ha unique su `(merchant_id, question_text)`: la stessa domanda genera una
   riga per turno (mai `frequency+1`, contrariamente all'intento dichiarato nel codice).
5. **La lista Knowledge Base non fa auto-poll** — `kb-doc-list.tsx` usa
   `refetchInterval: (data) => Array.isArray(data) && data.some(...)` ma sotto
   **TanStack Query v5** (5.99.2) quel callback riceve il *Query*, non i dati, quindi
   `Array.isArray(query)` è sempre `false` → il polling non parte MAI. Il worker ARQ
   indicizza il documento in ~1,5s (verificato in log + DB), ma la UI mostra "pending"
   finché l'utente non ricarica la pagina o non scatta un'altra invalidazione. Firma
   corretta v5: `(query) => query.state.data?.some(...) ? 3000 : false`. La suite ricarica
   la pagina per osservare il flip (mirroring di ciò che deve fare l'utente).

## Nota

I campi del pannello config sono selezionati via `id` = chiave puntata (es. `[id="bot.signature"]`),
gli input di login via `#email`/`#password`, il resto via testo italiano della UI. Se cambi le
label nei componenti, aggiorna gli spec.
