# ADR 0033 — "Avanza in pipeline" diventa anche un nodo standalone

**Data:** 2026-09-15 · **Stato:** accettata (implementata) · **Contesto:**
richiesta merchant — *"non vedo più 'Avanza in pipeline' tra le azioni
disponibili nelle automazioni"* → *"ok, deve essere un nodo a parte"*

## Contesto

L'azione non è mai stata un nodo a sé sulla lavagnetta. Da quando è nata
(commit `a1ee2ce`, 2026-06-23, insieme a `book_slot`/`update_score`/
`escalate_human`) è sempre stata solo una voce dentro **"Azioni AI
consentite"** del nodo `ai_reply` — cioè qualcosa che l'AI può decidere di
fare durante una risposta proattiva, non un blocco che il merchant piazza
sul grafo. Un commento in `engine.py` (`_do_set_lead_field`) lo diceva
esplicitamente: *"`stage` (a pipeline move) is intentionally out of scope
for V1 — use the move_pipeline action / ai_reply for that."*

Il merchant si aspettava un blocco deterministico, indipendente dall'AI —
esattamente come `human_handoff` esiste sia come nodo standalone sia come
azione AI-dispatchabile (`escalate_human`).

## Decisione

`move_pipeline` diventa **anche** un tipo di nodo azione standalone
(`ACTION_TYPES` in `db/models/automation.py`), in aggiunta — non in
sostituzione — alla voce AI-dispatchabile già esistente. Stesso handler
(`MovePipelineHandler`), stesso comportamento (stage di default =
`pipeline.qualified_stage_id`, crea l'opportunità se manca, nota GHL),
qualunque sia il chiamante.

Il nodo espone due campi opzionali, entrambi già presenti nel payload che
l'handler accetta:

| campo | vuoto = |
|---|---|
| `stage_id` | usa `pipeline.qualified_stage_id` configurato |
| `reason` | nessuna riga "Motivo" nella nota GHL |

## Perché così e non altrimenti

### Non riusare `ai_deps.dispatcher` (che il nodo `ai_reply` usa già per questa stessa azione)

Sarebbe stato il riuso più ovvio: il dispatcher esiste, sa già gestire
`move_pipeline`, e incapsula ogni handler in un `try/except`. Ma
`_LazyAiDeps` si risolve in `None` quando `_flow_uses_ai(automation)` è
falso (`engine.py:_build_ai_reply_deps`) — cioè in **esattamente** il caso
d'uso richiesto: un flusso con solo `trigger → move_pipeline`, senza alcun
nodo `ai_reply`/`ai_check`. Il nodo sarebbe stato un no-op silenzioso proprio
quando usato da solo. `_do_move_pipeline` costruisce quindi il proprio
`TurnContext`/`OrchestratorAction` e chiama `MovePipelineHandler` diretto,
con un `try/except` scritto a mano per ricreare la stessa rete di sicurezza
del dispatcher (una GHL down non deve abortire il resto del walk).

### Non estendere `set_lead_field` con `field: "stage"`

Il validatore di `set_lead_field` accetta già `"stage"` come valore di
`field` (leftover dormiente, mai implementato — il motore lo marca
`unsupported` e skippa). Estenderlo lì avrebbe richiesto comunque gli stessi
due campi (`stage_id`, `reason`) dentro un nodo generico "Aggiorna
lead/CRM", nascondendo di nuovo l'etichetta "Avanza in pipeline" che il
cliente cercava esplicitamente come voce a sé. Il leftover in `set_lead_field`
resta intatto e fuori scope.

### L'opzione AI-dispatch resta

Rimuoverla da `ALLOWED_ACTION_OPTIONS`/`AI_REPLY_DISPATCHABLE_ACTIONS`
avrebbe tolto al bot la possibilità di avanzare la pipeline autonomamente
quando decide che il lead è pronto — un comportamento diverso e già in
produzione, non richiesto in discussione. Stesso doppio schema già in uso
per `escalate_human` (AI-dispatchabile) e `human_handoff` (nodo esplicito).

## Conseguenze

- Nessuna migrazione: `ACTION_TYPES` è una tuple Python validata a runtime,
  non un enum di database.
- Nessun impatto sul percorso AI-dispatch esistente (`orchestrator.py`,
  `conversation_service.py`, l'iniezione deterministica su soglia).
- Il nodo funziona anche in un flusso senza alcun nodo AI — motivo per cui
  non passa da `ai_deps`.
