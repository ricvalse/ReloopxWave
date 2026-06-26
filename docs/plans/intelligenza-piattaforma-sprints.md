# Piano Implementazione: Intelligenza Piattaforma

**Data:** 2026-06-26  
**Stato:** Pianificazione  
**Scope:** 10 sprint indipendenti — ciascuno mergabile separatamente, nessuna dipendenza incrociata a runtime.

---

## Indice

| Sprint | Titolo | Effort | Priorità |
|--------|--------|--------|----------|
| [S-01](#s-01) | Native Tool-Use Loop + State Machine | L | 🔴 Alta |
| [S-02](#s-02) | RAG Intelligence (HyDE + re-ranking + gap detection) | M | 🔴 Alta |
| [S-03](#s-03) | Lead Scoring Intelligence (behavioral + decay + velocity) | M | 🔴 Alta |
| [S-04](#s-04) | Conversation Quality Guards (coherence + context compression) | S | 🟡 Media |
| [S-05](#s-05) | Pre-conversation Intelligence (send-time + intake scoring) | M | 🟡 Media |
| [S-06](#s-06) | Adaptive A/B Bandit (Thompson Sampling) | M | 🟡 Media |
| [S-07](#s-07) | Fine-tuning Intelligence (quality gate + drift + synthetic) | L | 🟡 Media |
| [S-08](#s-08) | Objection Intelligence (taxonomy + resolution rate + pre-emption) | M | 🟡 Media |
| [S-09](#s-09) | Escalation Intelligence (prediction + handoff brief + learning) | M | 🟢 Bassa |
| [S-10](#s-10) | Analytics Predittivi (anomaly + forecast + attribution + benchmark) | L | 🟢 Bassa |

**Effort key:** S = 1-2gg, M = 3-4gg, L = 5-7gg

---

## S-01 — Native Tool-Use Loop + Explicit State Machine {#s-01}

### Obiettivo
Sostituire l'approccio single-shot structured-JSON con un vero loop agentico dove il modello vede il risultato di ogni tool call prima di agire ulteriormente. Aggiungere una state machine esplicita per conversazione.

### Problema attuale
`orchestrator.py` fa: 1 LLM call → parsa JSON → esegue azione. Il modello non può ragionare sulla risposta del tool (es: "nessuno slot GHL disponibile → proponi alternativa"). Lo stato conversazionale è implicito nel contesto LLM, non ispezionabile né guidabile.

### File da toccare

**Backend:**
- `libs/ai_core/src/ai_core/orchestrator.py` — refactor del loop principale
- `libs/ai_core/src/ai_core/conversation_service.py` — iniettare state machine nel pipeline
- `libs/ai_core/src/ai_core/actions/` — ogni action ritorna un `ToolResult` strutturato
- **NEW** `libs/ai_core/src/ai_core/state_machine.py` — FSM con stati e transizioni
- `libs/db/src/db/models/conversation.py` — aggiungere campo `current_state`
- `libs/db/src/db/migrations/versions/0030_conversation_state.py` — nuova migration

### Task

1. **Definire gli stati FSM** in `state_machine.py`:
   ```
   GREETING → QUALIFYING → PITCHING → OBJECTION_HANDLING → CLOSING → BOOKED | DEAD | ESCALATED
   ```
   Ogni stato definisce: tool disponibili, system prompt suffix, soglia escalation.

2. **Refactor orchestrator** in loop `while not done` (max 5 iterazioni):
   ```python
   async def run_turn(ctx, conversation_id, user_message):
       state = await load_state(conversation_id)
       for _ in range(MAX_TOOL_ITERATIONS):
           response = await llm.call_with_tools(messages, tools=state.available_tools)
           if response.is_text_only:
               break
           tool_result = await execute_tool(response.tool_call)
           messages.append(tool_result)
           state = state.transition(tool_result)
       await persist_state(conversation_id, state)
       return response.text
   ```

3. **Persistere lo stato** in `conversations.current_state` (VARCHAR, enum-mapped).

4. **Migration 0030**: `ALTER TABLE conversations ADD COLUMN current_state VARCHAR(32) DEFAULT 'GREETING'`.

5. **Typing indicator 360dialog**: inviare `typing_on` all'inizio del loop e `typing_off` prima della risposta finale (già supportato da `d360_client.py`).

6. **Test unitari**: `tests/unit/test_state_machine.py` — coprire tutte le transizioni lecite e le transizioni invalide.

### Acceptance criteria
- [ ] Conversazione con prenotazione GHL slot non disponibile → il bot propone alternativa nello stesso turno senza errore
- [ ] `conversations.current_state` si aggiorna ad ogni turno
- [ ] Max 5 tool iterations per turno, poi il bot risponde con il contesto disponibile
- [ ] Nessuna regressione sui test esistenti `tests/unit/`

---

## S-02 — RAG Intelligence {#s-02}

### Obiettivo
Migliorare recall e precision del retrieval KB con tre tecniche indipendenti: HyDE, cross-encoder re-ranking, KB gap detection automatica. Aggiungere freshness decay nei chunk.

### File da toccare

**Backend:**
- `libs/ai_core/src/ai_core/rag/retriever.py` — HyDE + freshness decay nel ranking
- **NEW** `libs/ai_core/src/ai_core/rag/reranker.py` — cross-encoder via LLM (no dipendenza ML pesante)
- **NEW** `libs/ai_core/src/ai_core/rag/gap_detector.py` — rilevamento domande senza risposta
- `libs/db/src/db/models/kb.py` — aggiungere `last_updated_at` a `kb_chunks`
- **NEW** `libs/db/src/db/models/kb_gap.py` — tabella `kb_gaps`
- `libs/db/src/db/migrations/versions/0031_kb_intelligence.py`
- `workers/scheduler/kb_reindex.py` — schedulare gap detection post-conversazione

### Task

1. **HyDE in `retriever.py`**:
   ```python
   async def retrieve(query: str, merchant_id: str) -> list[Chunk]:
       # genera risposta ipotetica al posto della query raw
       hypothetical = await llm.complete(
           f"Rispondi brevemente in italiano: {query}",
           model="gpt-4.1-nano", max_tokens=150
       )
       query_vector = await embedder.embed(hypothetical)
       return await db.vector_search(query_vector, merchant_id, top_k=20)
   ```

2. **Cross-encoder re-ranking** (LLM-based, zero dipendenze nuove):
   ```python
   # reranker.py — chiama gpt-4.1-nano con tutti i chunk e la query
   # ritorna i top-5 con score 0-10
   async def rerank(query: str, chunks: list[Chunk]) -> list[Chunk]:
       ...
   ```
   Usare l'LLM come re-ranker è più lento ma evita di shippare sentence-transformers nel worker. Costo: ~200 token extra per turno.

3. **Freshness decay** nel ranking pgvector:
   ```sql
   -- weighted_score = cosine_similarity * EXP(-0.01 * days_since_update)
   SELECT *, (1 - (embedding <=> $1)) * EXP(-0.01 * EXTRACT(EPOCH FROM NOW() - last_updated_at)/86400)
   AS weighted_score FROM kb_chunks
   ORDER BY weighted_score DESC LIMIT 20;
   ```

4. **KB gap detection** — task ARQ `detect_kb_gaps(merchant_id, conversation_id)`:
   - Analizza i turni dove il retriever ha restituito 0 chunk rilevanti (similarity < 0.7)
   - Estrae la domanda del lead in quel turno
   - Inserisce in `kb_gaps(merchant_id, question_text, frequency, last_seen_at)`
   - Si aggiunge a `close_idle_conversations` cron (già esiste)

5. **API endpoint** `GET /knowledge-base/gaps` per esporre i gap al merchant dashboard.

6. **Migration 0031**:
   ```sql
   ALTER TABLE kb_chunks ADD COLUMN last_updated_at TIMESTAMPTZ DEFAULT NOW();
   CREATE TABLE kb_gaps (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     merchant_id UUID NOT NULL REFERENCES merchants(id),
     question_text TEXT NOT NULL,
     frequency INT DEFAULT 1,
     last_seen_at TIMESTAMPTZ DEFAULT NOW(),
     resolved BOOLEAN DEFAULT FALSE
   );
   ```

### Acceptance criteria
- [ ] HyDE attivo per default, fallback su query raw se LLM call fallisce
- [ ] Re-ranking riduce chunk da 20 a top-5 prima di passare al contesto LLM
- [ ] `kb_gaps` si popola dopo conversazioni con domande senza risposta
- [ ] `GET /knowledge-base/gaps` ritorna lista ordinata per frequency
- [ ] Chunk aggiornati nelle ultime 24h hanno score ≥ chunk vecchi di 30gg a parità di cosine similarity

---

## S-03 — Lead Scoring Intelligence {#s-03}

### Obiettivo
Arricchire il sistema di scoring cumulativo con segnali comportamentali WhatsApp (latenza, read receipts, lunghezza messaggi), decay temporale e velocity scoring basato su progressione di stato.

### File da toccare

**Backend:**
- `libs/ai_core/src/ai_core/scoring.py` — aggiungere behavioral features + decay
- `libs/db/src/db/models/lead.py` — nuovi campi behavioral
- `workers/conversation/handlers.py` — estrarre segnali da ogni messaggio WA in ingresso
- `workers/scheduler/kpi_rollup.py` — calcolo decay + velocity su tutti i lead attivi
- `libs/db/src/db/migrations/versions/0032_lead_scoring_signals.py`

### Task

1. **Estrarre segnali comportamentali** in `handlers.py` ad ogni webhook 360dialog:
   ```python
   # status webhook: "delivered", "read"
   if event.type == "message_status" and event.status == "read":
       await lead_repo.update_read_receipt(lead_id, read_at=event.timestamp)
   
   # messaggio in ingresso: calcolare latency
   if event.type == "message":
       latency = event.timestamp - last_bot_message_at
       msg_length = len(event.text)
       await lead_repo.update_behavioral_signals(lead_id, latency, msg_length)
   ```

2. **Schema `leads` aggiuntivo**:
   ```sql
   ALTER TABLE leads ADD COLUMN avg_response_latency_seconds INT;
   ALTER TABLE leads ADD COLUMN avg_message_length_chars INT;
   ALTER TABLE leads ADD COLUMN read_receipt_ratio FLOAT;  -- (messaggi letti / messaggi inviati)
   ALTER TABLE leads ADD COLUMN last_interaction_at TIMESTAMPTZ;
   ```

3. **Score decay** in `kpi_rollup.py` (cron giornaliero già esistente):
   ```python
   # effective_score = raw_score * exp(-0.02 * days_since_last_interaction)
   await db.execute("""
       UPDATE leads SET effective_score = 
           score * EXP(-0.02 * EXTRACT(EPOCH FROM NOW() - last_interaction_at) / 86400)
       WHERE status NOT IN ('booked', 'dead')
   """)
   ```
   Esporre `effective_score` nell'API invece di `score` raw dove rilevante.

4. **Velocity scoring**:
   - Calcolare tempo mediano (P50 per merchant) di progressione `GREETING → BOOKED`
   - Per ogni lead attivo: `velocity_ratio = median_time / actual_time_in_current_state`
   - `velocity_ratio > 2.0` → flag `high_velocity` (candidato fast-track)
   - `velocity_ratio < 0.5` → flag `stalled` (candidato reactivation)
   - Persistere in `leads.velocity_flag` (VARCHAR: `high|normal|stalled`)

5. **Aggiornare `scoring.py`** per combinare:
   ```
   final_score = w1 * conversation_score
               + w2 * behavioral_score(latency, msg_len, read_ratio)
               + w3 * velocity_score
   ```
   Pesi configurabili nel config_resolver (nuove chiavi `scoring_weight_conversation`, etc.).

6. **Migration 0032** con i campi sopra + index su `last_interaction_at`.

### Acceptance criteria
- [ ] Ogni messaggio WA in ingresso aggiorna `avg_response_latency_seconds` e `avg_message_length_chars`
- [ ] `effective_score` decade esponenzialmente per lead inattivi (verificabile con query SQL)
- [ ] Lead con latency < 60s e read_ratio > 0.9 hanno behavioral_score > 70
- [ ] `velocity_flag` si calcola nel cron giornaliero
- [ ] Nessun field aggiunto rompe lo schema Pydantic esistente (tutti nullable con default)

---

## S-04 — Conversation Quality Guards {#s-04}

### Obiettivo
Evitare che l'AI contraddica fatti già stabiliti (coherence guard) e comprimere automaticamente le conversazioni lunghe (>30 turni) in un memory block persistente.

### File da toccare

**Backend:**
- `libs/ai_core/src/ai_core/conversation_service.py` — iniettare i due guard nel pipeline
- **NEW** `libs/ai_core/src/ai_core/quality/coherence.py` — coherence checker
- **NEW** `libs/ai_core/src/ai_core/quality/compressor.py` — context summarizer
- `libs/db/src/db/models/conversation.py` — aggiungere `context_summary` JSONB
- `libs/db/src/db/migrations/versions/0033_conversation_quality.py`

### Task

1. **Coherence guard** (`coherence.py`):
   ```python
   async def check_coherence(history: list[Message], proposed_reply: str) -> CoherenceResult:
       # micro-prompt gpt-4.1-nano ~200 token
       prompt = f"""
       Conversazione precedente:
       {format_history(history[-10:])}
       
       Risposta proposta: {proposed_reply}
       
       La risposta contraddice fatti stabiliti nella conversazione? 
       Rispondi JSON: {{"coherent": bool, "issue": str|null}}
       """
       result = await llm.complete(prompt, model="gpt-4.1-nano", max_tokens=80)
       return parse_json(result)
   ```
   Se `coherent=False` → rigenerare la risposta (max 1 retry). Loggare via structlog.

2. **Context compressor** (`compressor.py`) — si attiva quando `len(messages) > COMPRESS_THRESHOLD` (default: 30):
   ```python
   async def compress_context(messages: list[Message]) -> MemoryBlock:
       older = messages[:-10]  # conserva gli ultimi 10 turni raw
       summary_prompt = f"""
       Riassumi questa conversazione estraendo:
       - Nome e dati chiave del lead
       - Prodotto/servizio di interesse
       - Budget comunicato
       - Obiezioni sollevate
       - Accordi già presi
       
       Conversazione: {format_history(older)}
       """
       summary = await llm.complete(summary_prompt, model="gpt-4.1-mini", max_tokens=300)
       return MemoryBlock(text=summary, compressed_turns=len(older))
   ```
   Il memory block sostituisce i turni vecchi nel contesto ma viene persistito in `conversations.context_summary`.

3. **Migration 0033**:
   ```sql
   ALTER TABLE conversations ADD COLUMN context_summary JSONB;
   -- {"text": "...", "compressed_turns": 42, "compressed_at": "2026-..."}
   ```

4. **Integration nel pipeline** in `conversation_service.py`:
   ```python
   messages = await load_messages(conversation_id)
   if len(messages) > COMPRESS_THRESHOLD:
       memory_block = await compressor.compress_context(messages)
       await save_summary(conversation_id, memory_block)
       messages = [memory_block_as_system_msg(memory_block)] + messages[-10:]
   
   reply = await orchestrator.run_turn(messages, user_message)
   
   if COHERENCE_GUARD_ENABLED:
       result = await coherence.check_coherence(messages, reply)
       if not result.coherent:
           reply = await orchestrator.run_turn(messages, user_message, retry=True)
   ```

5. **Config keys** nuove nel config_resolver: `coherence_guard_enabled` (bool, default true), `context_compress_threshold` (int, default 30).

### Acceptance criteria
- [ ] Conversazione con >30 turni: il context inviato all'LLM include il memory block + ultimi 10 turni
- [ ] `context_summary` persistito in DB dopo la compressione
- [ ] Risposta incoerente (test: dire al lead un nome diverso da quello che aveva dato) → viene rigenerata
- [ ] Coherence guard non blocca il turno se LLM guard call va in timeout (fail open)

---

## S-05 — Pre-conversation Intelligence {#s-05}

### Obiettivo
Calcolare il momento ottimale di invio per ogni lead e assegnare un intent score al momento dell'intake, prima ancora che la conversazione inizi.

### File da toccare

**Backend:**
- **NEW** `workers/scheduler/send_time.py` — ottimizzazione orario invio
- `workers/conversation/handlers.py` — intake scoring sul primo messaggio WA
- `libs/db/src/db/models/lead.py` — `optimal_send_hour`, `intake_score`
- `libs/db/src/db/migrations/versions/0034_pre_conversation_intel.py`
- `workers/settings.py` — registrare nuovo cron `optimize_send_times`

### Task

1. **Optimal send-time** — cron `optimize_send_times` (settimanale, domenica 06:00):
   ```python
   async def optimize_send_times(ctx):
       # per ogni merchant, per ogni lead attivo:
       # analizzare timestamp dei messaggi in ingresso delle ultime 4 settimane
       # costruire istogramma orario (24 bucket)
       # l'ora con più messaggi = optimal_send_hour
       leads = await lead_repo.get_active_with_history(merchant_id)
       for lead in leads:
           hist = build_hourly_histogram(lead.message_timestamps)
           optimal_hour = hist.argmax()
           await lead_repo.update(lead.id, optimal_send_hour=optimal_hour)
   ```

2. **Usare `optimal_send_hour`** nel no_answer scheduler (`workers/scheduler/no_answer.py`):
   ```python
   # invece di inviare subito il follow-up, schedulare per l'ora ottimale del giorno
   if lead.optimal_send_hour:
       send_at = next_occurrence_of_hour(lead.optimal_send_hour, tz=lead.timezone)
       await queue.enqueue_at(send_followup, lead_id, eta=send_at)
   ```

3. **Intake scoring** in `handlers.py` — si attiva al primo messaggio di un lead nuovo:
   ```python
   async def score_lead_at_intake(lead: Lead, first_message: str, source_metadata: dict) -> int:
       prompt = f"""
       Primo messaggio: "{first_message}"
       Sorgente: {source_metadata.get('source', 'unknown')}
       Ora: {datetime.now().hour}:00
       
       Assegna un intent score 0-100. Alto = lead pronto all'acquisto.
       Rispondi solo con il numero intero.
       """
       score = int(await llm.complete(prompt, model="gpt-4.1-nano", max_tokens=5))
       return max(0, min(100, score))
   ```
   Persistere in `leads.intake_score`. Usarlo come `initial_score` nel cumulative scorer.

4. **Migration 0034**:
   ```sql
   ALTER TABLE leads ADD COLUMN optimal_send_hour SMALLINT;  -- 0-23, nullable
   ALTER TABLE leads ADD COLUMN intake_score SMALLINT;
   ALTER TABLE leads ADD COLUMN message_timestamps TIMESTAMPTZ[];  -- per calcolo istogramma
   ```

5. **Esporre `optimal_send_hour` e `intake_score`** nell'API lead detail.

### Acceptance criteria
- [ ] Cron settimanale aggiorna `optimal_send_hour` per lead con ≥5 messaggi storici
- [ ] Follow-up schedulato rispetta `optimal_send_hour` se definito
- [ ] `intake_score` viene calcolato entro 2s dal primo messaggio di un lead nuovo
- [ ] Lead con `intake_score > 80` ricevono risposta immediata (max_delay = 0 nel no_answer scheduler)

---

## S-06 — Adaptive A/B Bandit (Thompson Sampling) {#s-06}

### Obiettivo
Sostituire lo split A/B statico 50/50 con un bandit adattivo Thompson Sampling che instrada automaticamente più traffico alla variante migliore, aggiornandosi in tempo reale.

### File da toccare

**Backend:**
- **NEW** `libs/ai_core/src/ai_core/bandit.py` — Thompson Sampling engine
- `workers/conversation/handlers.py` — routing della variante via bandit invece di random split
- `libs/db/src/db/models/ab.py` — aggiungere `alpha_param`, `beta_param` per variante
- `services/api/src/api/routers/ab_test.py` — esporre distribuzione Beta nel GET
- `libs/db/src/db/migrations/versions/0035_bandit_params.py`

### Task

1. **Bandit engine** (`bandit.py`):
   ```python
   import random
   
   def thompson_sample(alpha: float, beta: float) -> float:
       # campionamento dalla distribuzione Beta
       # implementazione manuale con il metodo di Johnk (no scipy richiesto)
       return _beta_sample(alpha, beta)
   
   async def select_variant(ab_test_id: str, db) -> str:
       variants = await db.get_ab_variants(ab_test_id)  # con alpha, beta
       samples = {v.id: thompson_sample(v.alpha_param, v.beta_param) for v in variants}
       return max(samples, key=samples.get)
   
   async def update_variant(variant_id: str, converted: bool, db):
       if converted:
           await db.increment_alpha(variant_id)  # alpha += 1 (successo)
       else:
           await db.increment_beta(variant_id)   # beta += 1 (fallimento)
   ```

2. **Beta sampling senza scipy** (implementazione Johnk):
   ```python
   def _beta_sample(alpha: float, beta: float) -> float:
       # usa random.gammavariate disponibile in stdlib
       x = random.gammavariate(alpha, 1)
       y = random.gammavariate(beta, 1)
       return x / (x + y)
   ```

3. **Migration 0035**:
   ```sql
   ALTER TABLE ab_test_variants 
     ADD COLUMN alpha_param FLOAT DEFAULT 1.0,
     ADD COLUMN beta_param FLOAT DEFAULT 1.0;
   -- inizializzare le varianti esistenti con (1, 1) = prior uniforme
   UPDATE ab_test_variants SET alpha_param = 1.0, beta_param = 1.0;
   ```

4. **Routing** in `handlers.py`:
   ```python
   # prima: variant = random.choice(variants)
   # dopo:
   variant_id = await bandit.select_variant(ab_test_id, db)
   
   # a conversazione conclusa (lead converted o dead):
   converted = lead.status == "booked"
   await bandit.update_variant(conversation.variant_id, converted, db)
   ```

5. **API update** — `GET /ab-tests/{id}` include:
   ```json
   {
     "variants": [
       {"id": "...", "name": "A", "alpha": 12.0, "beta": 5.0, "win_probability": 0.71}
     ]
   }
   ```
   `win_probability` = frazione delle ultime 1000 simulazioni Thompson in cui questa variante vince (calcolato server-side).

6. **Early stopping**: se `win_probability > 0.95` per 3 giorni consecutivi → segnale `conclusione_anticipata` nel test (non auto-stop: decisione al merchant).

### Acceptance criteria
- [ ] Dopo 100 conversazioni con variante A conversion 40% e variante B 20%, variante A riceve ≥65% del traffico
- [ ] `alpha_param` e `beta_param` si aggiornano ad ogni conversazione conclusa
- [ ] Il routing è statisticamente corretto (test: distribuzione campionata converge al prior con 0 dati)
- [ ] API espone `win_probability` calcolata live

---

## S-07 — Fine-tuning Intelligence {#s-07}

### Obiettivo
Migliorare la qualità del dataset FT con un quality gate automatico, aggiungere drift detection che suggerisce quando rilanciare il fine-tuning, e generare esempi sintetici per scenari rari.

### File da toccare

**Backend:**
- **NEW** `workers/fine_tuning/quality_gate.py` — scoring qualità conversazioni
- **NEW** `workers/fine_tuning/drift_detector.py` — rilevamento drift metriche
- **NEW** `workers/fine_tuning/synthetic.py` — generazione esempi sintetici
- `workers/fine_tuning/collect.py` — integrare quality gate nella collection
- `workers/fine_tuning/handlers.py` — aggiungere `check_ft_drift` come task ARQ
- `workers/settings.py` — registrare `check_ft_drift` cron (settimanale)
- `libs/db/src/db/migrations/versions/0036_ft_intelligence.py`

### Task

1. **Quality gate** (`quality_gate.py`):
   ```python
   QUALITY_THRESHOLD = 0.65
   
   async def score_conversation_for_ft(conv_id: str, db) -> float:
       conv = await db.get_conversation_with_messages(conv_id)
       lead = await db.get_lead(conv.lead_id)
       
       scores = []
       # Segnale 1: il lead si è convertito?
       scores.append(1.0 if lead.status == "booked" else 0.2)
       # Segnale 2: nessuna escalazione umana durante la conversazione?
       scores.append(0.0 if conv.had_human_escalation else 1.0)
       # Segnale 3: il lead ha risposto almeno 3 volte (non monologo del bot)?
       lead_turns = sum(1 for m in conv.messages if m.role == "user")
       scores.append(min(lead_turns / 3, 1.0))
       # Segnale 4: assenza di messaggi molto corti del bot (<20 char = probabile errore)?
       short_bot = sum(1 for m in conv.messages if m.role == "assistant" and len(m.content) < 20)
       scores.append(1.0 if short_bot == 0 else max(0.0, 1 - short_bot * 0.3))
       
       return sum(scores) / len(scores)
   ```
   Modificare `collect.py`: includere solo conversazioni con score ≥ `QUALITY_THRESHOLD`.

2. **Drift detector** (`drift_detector.py`) — cron settimanale `check_ft_drift`:
   ```python
   async def check_ft_drift(ctx, merchant_id: str):
       # calcola conversion_rate ultime 2 settimane vs media mobile 8 settimane
       recent = await analytics_repo.get_conversion_rate(merchant_id, days=14)
       baseline = await analytics_repo.get_conversion_rate(merchant_id, days=56)
       
       if baseline > 0 and (baseline - recent) / baseline > 0.15:
           # calo >15% → creare notifica + segnare in ft_drift_alerts
           await alert_repo.create_ft_drift_alert(merchant_id, recent, baseline)
           # opzionale: auto-enqueue fine_tune_run se drift > 25%
           if (baseline - recent) / baseline > 0.25:
               await queue.enqueue("fine_tune_run", merchant_id=merchant_id)
   ```

3. **Synthetic augmentation** (`synthetic.py`) — task on-demand `generate_synthetic_examples`:
   ```python
   async def generate_synthetic_examples(ctx, merchant_id: str, objection_type: str, n: int = 10):
       kb_context = await rag.retrieve(f"Come rispondere a: {objection_type}", merchant_id)
       
       for _ in range(n):
           example = await llm.complete(f"""
           Genera un esempio di conversazione WhatsApp (4-8 turni) dove:
           - Il lead solleva l'obiezione: "{objection_type}"
           - L'AI la gestisce con successo portando alla prenotazione
           - Usa un tono naturale, colloquiale, con piccoli errori di battitura
           - Contesto prodotto: {kb_context}
           
           Formato JSONL per OpenAI fine-tuning.
           """, model="gpt-4.1-mini")
           await ft_dataset_repo.insert_synthetic(merchant_id, example, objection_type)
   ```

4. **Curriculum ordering** in `export.py`:
   ```python
   # ordinare il dataset per complessità crescente prima dell'export
   def sort_by_curriculum(examples: list[FTExample]) -> list[FTExample]:
       def complexity(ex): 
           return len(ex.messages) + ex.num_objections * 3
       return sorted(examples, key=complexity)
   ```

5. **Migration 0036**:
   ```sql
   ALTER TABLE ft_training_examples ADD COLUMN quality_score FLOAT;
   ALTER TABLE ft_training_examples ADD COLUMN is_synthetic BOOLEAN DEFAULT FALSE;
   CREATE TABLE ft_drift_alerts (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     merchant_id UUID NOT NULL REFERENCES merchants(id),
     recent_rate FLOAT, baseline_rate FLOAT,
     created_at TIMESTAMPTZ DEFAULT NOW(),
     acknowledged BOOLEAN DEFAULT FALSE
   );
   ```

6. **API** `GET /fine-tuning/drift-alerts` per merchant (mostrare in dashboard se alert attivo).

### Acceptance criteria
- [ ] Solo conversazioni con quality_score ≥ 0.65 entrano nel dataset FT
- [ ] Cron settimanale genera `ft_drift_alerts` quando conversion_rate cala >15%
- [ ] `generate_synthetic_examples` produce esempi JSONL validi per OpenAI FT API
- [ ] Dataset esportato è ordinato per complessità crescente

---

## S-08 — Objection Intelligence {#s-08}

### Obiettivo
Trasformare le obiezioni estratte da dati grezzi a conoscenza strutturata: clustering automatico in categorie, resolution rate per tipo, e iniezione preventiva delle risposte migliori prima che l'obiezione venga sollevata.

### File da toccare

**Backend:**
- **NEW** `workers/scheduler/objection_clustering.py` — clustering automatico
- `libs/ai_core/src/ai_core/objections.py` — aggiungere prediction + preemption
- `libs/db/src/db/models/` — **NEW** `objection_cluster.py`
- `services/api/src/api/routers/reports.py` — endpoint cluster + resolution rate
- `workers/settings.py` — registrare `cluster_objections` cron (settimanale)
- `libs/db/src/db/migrations/versions/0037_objection_intelligence.py`

### Task

1. **Clustering LLM-based** (niente scikit-learn — usiamo l'LLM come classificatore zero-shot):
   ```python
   async def cluster_objections(ctx, merchant_id: str):
       # recupera tutte le obiezioni grezze non ancora clustrate
       raw = await objection_repo.get_unclustered(merchant_id)
       
       # fase 1: chiedere all'LLM di raggruppare in ≤10 categorie
       categories_prompt = f"""
       Analizza queste obiezioni di vendita e raggrupale in massimo 10 categorie.
       Obiezioni: {[o.text for o in raw]}
       Rispondi JSON: [{{"category": str, "label_it": str, "objection_ids": [str]}}]
       """
       clusters = await llm.complete(categories_prompt, model="gpt-4.1-mini")
       
       # fase 2: persistere le categorie e aggiornare ogni obiezione con cluster_id
       for cluster in parse_json(clusters):
           cluster_id = await objection_cluster_repo.upsert(merchant_id, cluster["label_it"])
           await objection_repo.assign_cluster(cluster["objection_ids"], cluster_id)
   ```

2. **Resolution rate per cluster** — calcolato in `kpi_rollup.py`:
   ```sql
   -- per ogni cluster: quante conversazioni con quell'obiezione si sono concluse in 'booked'?
   SELECT oc.id, oc.label, 
          COUNT(*) FILTER (WHERE l.status = 'booked') * 1.0 / COUNT(*) AS resolution_rate
   FROM objection_clusters oc
   JOIN objections o ON o.cluster_id = oc.id
   JOIN conversations c ON c.id = o.conversation_id
   JOIN leads l ON l.id = c.lead_id
   GROUP BY oc.id, oc.label;
   ```
   Persistere in `objection_clusters.resolution_rate`.

3. **Best response per cluster** — `objection_repo.get_best_responses(cluster_id)`:
   - Recuperare le risposte AI al tipo di obiezione nelle conversazioni che si sono concluse con `booked`
   - Ordinare per lunghezza adeguata e lead score finale
   - Ritornare le top-3 come esempi

4. **Pre-emptive injection** in `conversation_service.py`:
   ```python
   # PRIMA di chiamare il modello, predire se il prossimo messaggio potrebbe essere un'obiezione
   predicted_cluster = await objections.predict_next_objection(conversation_history)
   if predicted_cluster and predicted_cluster.probability > 0.6:
       preemptive_context = await objection_repo.get_best_responses(predicted_cluster.id)
       system_prompt += f"\n\n[Contesto preventivo: il lead potrebbe sollevare '{predicted_cluster.label}'. "
                       f"Risposte che hanno funzionato in passato: {preemptive_context}]"
   ```

5. **Migration 0037**:
   ```sql
   CREATE TABLE objection_clusters (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     merchant_id UUID NOT NULL REFERENCES merchants(id),
     label VARCHAR(128) NOT NULL,
     resolution_rate FLOAT,
     occurrence_count INT DEFAULT 0,
     updated_at TIMESTAMPTZ DEFAULT NOW()
   );
   ALTER TABLE objections ADD COLUMN cluster_id UUID REFERENCES objection_clusters(id);
   ```

6. **API**:
   - `GET /reports/objection-clusters` → lista cluster con occurrence_count e resolution_rate
   - `GET /reports/objection-clusters/{id}/best-responses` → top-3 risposte vincenti

### Acceptance criteria
- [ ] Cron settimanale produce ≤10 cluster per merchant con ≥20 obiezioni
- [ ] Ogni cluster ha `resolution_rate` calcolato
- [ ] Pre-emption si attiva quando `predicted_probability > 0.6`
- [ ] API restituisce cluster ordinati per `occurrence_count DESC`

---

## S-09 — Escalation Intelligence {#s-09}

### Obiettivo
Predire la necessità di escalation 2-3 turni prima che avvenga, generare automaticamente un brief strutturato per l'operatore umano, e catturare le risoluzioni umane come esempi per la KB.

### File da toccare

**Backend:**
- **NEW** `libs/ai_core/src/ai_core/escalation.py` — prediction + handoff brief
- `libs/ai_core/src/ai_core/conversation_service.py` — integrare prediction nel pipeline
- `workers/conversation/handlers.py` — catturare post-escalation learning
- `services/api/src/api/routers/conversations.py` — endpoint per handoff brief
- `libs/db/src/db/migrations/versions/0038_escalation_intelligence.py`

### Task

1. **Escalation prediction** (`escalation.py`):
   ```python
   ESCALATION_FEATURES = {
       "negative_sentiment_streak": 3,   # 3 turni consecutivi con sentiment negativo
       "same_objection_repeated": 2,     # stessa obiezione sollevata 2+ volte
       "off_topic_turns": 2,             # 2+ turni fuori scope KB
       "explicit_human_request": True,   # "voglio parlare con un umano"
   }
   
   async def predict_escalation_probability(conversation_id: str, db) -> float:
       messages = await db.get_recent_messages(conversation_id, limit=6)
       sentiments = [m.sentiment_score for m in messages if m.role == "user"]
       
       features = {
           "negative_sentiment_streak": sum(1 for s in sentiments[-3:] if s < 0.3),
           "repeated_objections": await count_repeated_objections(conversation_id, db),
           "explicit_request": any("umano" in m.content or "persona" in m.content 
                                   for m in messages if m.role == "user"),
       }
       # regressione logistica semplice (pesi hardcoded, calibrati empiricamente)
       score = (features["negative_sentiment_streak"] * 0.25 +
                features["repeated_objections"] * 0.35 +
                features["explicit_request"] * 0.80)
       return min(score, 1.0)
   ```
   Se `probability > 0.7` → emettere notifica push all'operatore (Supabase Realtime).

2. **Handoff brief** — generato automaticamente al momento dell'escalation:
   ```python
   async def generate_handoff_brief(conversation_id: str, db) -> HandoffBrief:
       conv = await db.get_conversation_with_messages(conversation_id)
       lead = await db.get_lead(conv.lead_id)
       
       brief = await llm.complete(f"""
       Genera un brief per l'operatore umano che prenderà questa conversazione:
       
       Conversazione (ultimi 10 turni):
       {format_messages(conv.messages[-10:])}
       
       Genera JSON con:
       - lead_name, lead_score, product_interest
       - main_objection (la più recente)
       - conversation_stage
       - recommended_action (1 frase concreta)
       - tone_note (come approcciare questo specifico lead)
       """, model="gpt-4.1-mini")
       
       return HandoffBrief(**parse_json(brief))
   ```
   Persistere in `escalations.handoff_brief` JSONB. Esporre via `GET /conversations/{id}/handoff-brief`.

3. **Post-escalation learning** — webhook/trigger quando una conversazione escalata viene chiusa dall'operatore con status `booked`:
   ```python
   async def capture_human_resolution(conversation_id: str, db):
       # recupera i messaggi che l'operatore umano ha inviato
       human_messages = await db.get_human_agent_messages(conversation_id)
       
       # candidati KB: messaggi umani lunghi (>100 char) che hanno sbloccato la conversazione
       for msg in human_messages:
           if len(msg.content) > 100:
               await kb_gap_repo.mark_resolved(
                   merchant_id=conversation.merchant_id,
                   resolution_text=msg.content,
                   source="human_agent"
               )
   ```

4. **Migration 0038**:
   ```sql
   CREATE TABLE escalations (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     conversation_id UUID NOT NULL REFERENCES conversations(id),
     predicted_at TIMESTAMPTZ,
     prediction_score FLOAT,
     triggered_at TIMESTAMPTZ DEFAULT NOW(),
     handoff_brief JSONB,
     resolved_by UUID REFERENCES users(id),
     resolved_at TIMESTAMPTZ
   );
   ```

5. **Notifica Realtime** (Supabase `INSERT` trigger su `escalations` → Realtime channel `merchant:{merchant_id}:escalations`).

### Acceptance criteria
- [ ] Prediction si attiva ≤3 turni prima dell'escalation effettiva (misurabile in replay storico)
- [ ] Handoff brief generato in ≤3s dall'escalation
- [ ] Operatore vede il brief nell'UI conversazioni prima ancora di aprire la chat
- [ ] Messaggi umani post-escalation diventano candidati KB gap risolti

---

## S-10 — Analytics Predittivi {#s-10}

### Obiettivo
Aggiungere anomaly detection sulle KPI (alerting proattivo), revenue forecasting, attribution dei chunk KB ai risultati, e benchmarking anonimizzato tra merchant dello stesso vertical.

### File da toccare

**Backend:**
- `workers/scheduler/kpi_rollup.py` — aggiungere anomaly detection post-rollup
- **NEW** `workers/scheduler/revenue_forecast.py` — forecasting settimanale
- **NEW** `workers/scheduler/kb_attribution.py` — attribution post-conversione
- **NEW** `workers/scheduler/merchant_benchmark.py` — benchmark anonimizzato
- `services/api/src/api/routers/analytics.py` — nuovi endpoint
- `libs/db/src/db/migrations/versions/0039_predictive_analytics.py`

### Task

1. **Anomaly detection** — aggiunto alla fine di `kpi_rollup.py`:
   ```python
   async def detect_kpi_anomalies(ctx, merchant_id: str):
       # recupera metriche giornaliere ultime 14 + 14 giorni precedenti
       recent_14 = await analytics_repo.get_daily_metrics(merchant_id, days=14)
       prior_14 = await analytics_repo.get_daily_metrics(merchant_id, days=28, offset=14)
       
       for metric in ["conversion_rate", "response_rate", "avg_turns_to_close"]:
           recent_avg = mean([d[metric] for d in recent_14])
           prior_avg = mean([d[metric] for d in prior_14])
           prior_std = stdev([d[metric] for d in prior_14]) or 0.01
           
           z_score = abs(recent_avg - prior_avg) / prior_std
           direction = "calo" if recent_avg < prior_avg else "aumento"
           
           if z_score > 2.0:
               await anomaly_repo.create(merchant_id, metric, z_score, direction, recent_avg, prior_avg)
   ```
   `statistics.mean` e `statistics.stdev` sono stdlib Python — zero dipendenze esterne.

2. **Revenue forecasting** — cron settimanale `forecast_revenue`:
   ```python
   async def forecast_revenue(ctx, merchant_id: str):
       active_leads = await lead_repo.get_active(merchant_id)
       avg_deal_value = await merchant_repo.get_avg_deal_value(merchant_id)
       
       # per bucket di score: stimare p(conversione) dal tasso storico
       buckets = {
           "hot": [l for l in active_leads if l.effective_score >= 70],
           "warm": [l for l in active_leads if 40 <= l.effective_score < 70],
           "cold": [l for l in active_leads if l.effective_score < 40],
       }
       historical_rates = await analytics_repo.get_conversion_rates_by_bucket(merchant_id)
       
       forecast = sum(
           len(bucket) * historical_rates.get(name, 0) * avg_deal_value
           for name, bucket in buckets.items()
       )
       await forecast_repo.upsert(merchant_id, forecast_eur=forecast, horizon_days=30)
   ```

3. **KB attribution** — `kb_attribution.py`, eseguito a conversazione chiusa con `booked`:
   ```python
   async def attribute_kb_chunks(conversation_id: str, db):
       # recupera tutti i chunk usati nel contesto durante la conversazione
       chunks_used = await rag_log_repo.get_chunks_for_conversation(conversation_id)
       
       for chunk in chunks_used:
           await kb_attribution_repo.increment(
               chunk_id=chunk.id,
               merchant_id=chunk.merchant_id,
               attributed_conversion=True
           )
   ```
   Richiede che `retriever.py` loggi i chunk usati in `rag_retrieval_log` (tabella nuova).

4. **Merchant benchmarking** (anonimizzato) — cron mensile:
   ```python
   async def compute_merchant_benchmarks(ctx):
       # raggruppa per vertical (campo su merchants)
       # calcola P25, P50, P75 per conversion_rate, avg_turns_to_close
       # NON esporre merchant_id singoli: solo aggregati con COUNT >= 5
       benchmarks = await analytics_repo.get_vertical_benchmarks(min_count=5)
       await benchmark_repo.upsert_all(benchmarks)
   ```
   Endpoint `GET /analytics/benchmark` ritorna la posizione percentile del merchant vs vertical.

5. **Migration 0039**:
   ```sql
   CREATE TABLE kpi_anomalies (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     merchant_id UUID NOT NULL REFERENCES merchants(id),
     metric VARCHAR(64), z_score FLOAT, direction VARCHAR(10),
     recent_value FLOAT, baseline_value FLOAT,
     created_at TIMESTAMPTZ DEFAULT NOW(), acknowledged BOOLEAN DEFAULT FALSE
   );
   CREATE TABLE revenue_forecasts (
     merchant_id UUID PRIMARY KEY REFERENCES merchants(id),
     forecast_eur FLOAT, horizon_days INT, computed_at TIMESTAMPTZ DEFAULT NOW()
   );
   CREATE TABLE rag_retrieval_log (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     conversation_id UUID REFERENCES conversations(id),
     chunk_id UUID REFERENCES kb_chunks(id),
     similarity_score FLOAT, created_at TIMESTAMPTZ DEFAULT NOW()
   );
   CREATE TABLE kb_chunk_attributions (
     chunk_id UUID PRIMARY KEY REFERENCES kb_chunks(id),
     conversion_count INT DEFAULT 0, total_uses INT DEFAULT 0
   );
   CREATE TABLE vertical_benchmarks (
     vertical VARCHAR(64), metric VARCHAR(64),
     p25 FLOAT, p50 FLOAT, p75 FLOAT, merchant_count INT,
     computed_at TIMESTAMPTZ DEFAULT NOW(),
     PRIMARY KEY (vertical, metric)
   );
   ```

6. **Nuovi endpoint analytics**:
   - `GET /analytics/anomalies` — lista anomalie attive (non acknowledged)
   - `GET /analytics/revenue-forecast` — previsione revenue 30gg
   - `GET /analytics/kb-attribution` — top chunk KB per conversioni
   - `GET /analytics/benchmark` — posizione percentile vs vertical

### Acceptance criteria
- [ ] Anomalia creata quando conversion_rate scende >2σ rispetto alle 2 settimane precedenti
- [ ] Revenue forecast aggiornato ogni settimana, errore medio ≤30% (verificabile in retrospettiva)
- [ ] KB attribution: ogni chunk ha `conversion_count` aggiornato dopo ogni `booked`
- [ ] Benchmark non espone merchant con < 5 merchant nel vertical (privacy floor)

---

## Note trasversali

### Migrazioni
Gli sprint producono migration `0030` → `0039`. Fare `uv run alembic upgrade head` nell'ordine degli sprint se eseguiti in sequenza, oppure uno alla volta se gli sprint sono paralleli (le migration sono indipendenti a livello schema).

### Config Resolver
Gli sprint S-01, S-04, S-05 aggiungono nuove chiavi al config resolver. Aggiungerle sempre in `libs/config_resolver/src/config_resolver/schema.py` con default ragionevoli prima di usarle nel codice. Non usare hardcoded magic numbers nel core.

### Test
- Ogni sprint deve includere almeno unit test per la nuova logica core
- Gli sprint che toccano handler ARQ devono avere un test di integrazione minimo (mock della queue)
- I guard (coherence, quality gate, escalation prediction) devono avere test di fail-open: il sistema funziona anche se il guard va in timeout

### Ordine consigliato di esecuzione
Se si vuole un ordine, partire da S-01 (tool loop) e S-02 (RAG) in parallelo — impattano direttamente la qualità del bot in produzione. S-04 e S-03 vengono subito dopo. Il resto in qualsiasi ordine.
