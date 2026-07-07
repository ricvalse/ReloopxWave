# E2E findings — 2026-07-07

Bug di piattaforma emersi montando la suite E2E Playwright (`e2e/`, contro produzione Railway)
che verifica (a) le risposte del bot al variare della configurazione e (b) il RAG sulla
knowledge base. Tutti riproducibili; la suite li aggira dove serve per restare verde e li
documenta inline. Ordine per impatto.

## 1. HyDE e re-ranking RAG rotti in produzione (alto)
`libs/ai_core/src/ai_core/llm.py:82-83` passa `max_tokens` alle Chat Completions, ma i modelli
reasoning `gpt-5.x` (in prod gira `gpt-5.4-mini-2026-03-17`) lo rifiutano con
`400 unsupported_parameter: 'max_tokens' ... Use 'max_completion_tokens' instead`.
`RAGEngine._hyde` e `_rerank` (e `objections.classify_objections`, visto crashare nei log worker)
catturano l'eccezione e ripiegano **in silenzio** (log a `debug`) sulla query grezza / ordine
originale. Effetto: **HyDE e rerank non funzionano per nessun tenant**; il RAG gira degradato.
Fix: mappare `max_tokens` → `max_completion_tokens` per i modelli che lo richiedono in
`LLMClient.complete`.

## 2. `rag.min_score` di default (0.7) irraggiungibile dalla query grezza (alto, consegue da 1)
Con `text-embedding-3-small`, una domanda quasi-verbatim sul contenuto del documento scora
~0.667 (misurato in prod: `Cosa include il pacchetto Onda Zaffiro…` vs il chunk = 0.6668).
Con HyDE rotto (bug 1) l'embedding resta quello della domanda grezza, sotto la soglia 0.7 →
`retrieve()` torna 0 chunk e la domanda finisce (anche a torto) in `kb_gaps`. Con HyDE
funzionante lo score sale (testo ipotetico simile al chunk ≈ 0.85), quindi **il fix di 1
risolve gran parte di 2**; valutare comunque un default di `rag.min_score` più basso (~0.5–0.6).

## 3. `bot.language` non rispettato dal modello (medio)
Il system prompt include `Rispondi sempre in lingua {language}` (`conversation_service.py:508`),
ma con `bot.language=en` e cliente che scrive in italiano il modello risponde comunque in
italiano (deterministico su più run). L'istruzione è troppo debole nel contesto del prompt
italiano. Fix: rafforzare (es. istruzione in testa, in maiuscolo, ripetuta) o forzare la lingua
di output esplicitamente. Test relativo in `test.skip` finché non risolto.

## 4. `kb_gaps` non deduplica (basso)
`RAGEngine._log_gap` fa `INSERT ... ON CONFLICT DO NOTHING`, ma `kb_gaps` non ha un vincolo
unique su `(merchant_id, question_text)`: ogni turno inserisce una riga nuova e `frequency`
resta sempre 1, contrariamente all'intento del codice (che vorrebbe incrementarla). Fix: unique
constraint + `ON CONFLICT (merchant_id, question_text) DO UPDATE SET frequency = frequency+1,
last_seen_at = now()`.

## 5. La lista Knowledge Base non fa auto-poll (medio, UX)
`apps/web-merchant/.../kb/kb-doc-list.tsx` usa
`refetchInterval: (data) => Array.isArray(data) && data.some(...) ? 3000 : false`, ma sotto
**TanStack Query v5** (5.99.2) il callback di `refetchInterval` riceve l'oggetto `Query`, non i
dati: `Array.isArray(query)` è sempre `false` → **il polling non parte mai**. Il worker ARQ
indicizza il documento in ~1,5s (verificato in log + DB), ma nella UI lo stato resta "pending"
finché l'utente non ricarica la pagina (o non scatta un'altra invalidazione). Fix: firma v5
`refetchInterval: (query) => query.state.data?.some((d) => d.status === 'pending' || d.status === 'indexing') ? 3000 : false`.

---

**Cosa funziona (verificato end-to-end):** login admin/merchant, creazione merchant + utente da
UI admin, impersonation, il playground come dry-run fedele, il fatto che la configurazione del
bot cambia le risposte (istruzioni aggiuntive, marker, ecc.), e l'intera catena RAG a livello di
dato (upload → embedding worker → `kb_chunks` → retrieval → risposta fondata sul documento).
I bug sopra sono degradi/edge, non rotture del percorso principale.
