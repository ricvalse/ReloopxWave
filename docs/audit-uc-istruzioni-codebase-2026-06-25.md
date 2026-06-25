# Audit Use Case — Capitolato vs ISTRUZIONI.md vs Codebase

**Data:** 2026-06-25  
**Scope:** 13 UC del capitolato (`reloop-ai-architettura.md`) + feature extra documentate in `ISTRUZIONI.md`  
**Metodo:** lettura spec → verifica ISTRUZIONI.md → ispezione file codebase (non stub-check)

---

## Sintesi esecutiva

| Area | Stato |
|---|---|
| Tutti i 13 UC presenti nel capitolato | ✅ |
| Tutti i 13 UC documentati in ISTRUZIONI.md | ✅ |
| Tutti i 13 UC con test oggettivo (Traccia A + B + SQL) | ✅ |
| Tutti i 13 UC implementati in codebase (nessun stub) | ✅ |
| Feature extra (Lavagnetta, Template WA, Handoff, Tool-use) | ✅ impl + ✅ test ISTRUZIONI |
| CI verde | ✅ ruff/mypy/vitest tutti passano (verificato 2026-06-25) |
| Fine-tuning (Fase 3 capitolato) | ✅ impl — ⚠️ escluso esplicitamente da ISTRUZIONI.md §12 |
| Export CSV (UC-11/12) | ✅ backend + ✅ UI pulsante cablato (2026-06-25) |
| Stale dirs frontend | ✅ rimosse (2026-06-25) |

**Zero UC mancanti** dalla spec, da ISTRUZIONI.md o dalla codebase. I gap residui sono operativi (CI, un pulsante UI) non funzionali.

---

## 1. Mappa UC capitolato → ISTRUZIONI.md → codebase

### UC-01 — First Response Istantaneo

**Capitolato:** rispondee su WhatsApp in secondi, qualifica lead, usa system prompt dal profilo attività.

**ISTRUZIONI.md §5.1:**
- Traccia A (WhatsApp reale): scenario completo con verifica in Conversazioni e SQL (`messages` role user/assistant).
- Traccia B (Playground): verifica riflesso profilo attività.
- Variante negativa: `auto_reply_enabled OFF` → solo riga user, nessuna risposta bot.
- Test gate `auto_reply_enabled` chiaramente documentato.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `workers/conversation/handlers.py` | 936 | ✅ Implementato — inbound WA, debounce, GHL events, outbound composer |
| `libs/ai_core/src/ai_core/conversation_service.py` | 1562 | ✅ Implementato — pipeline completa: tenant, lead, RAG, orchestrator, persistenza, dispatch azioni |
| `libs/ai_core/src/ai_core/orchestrator.py` | 423 | ✅ Implementato — loop tool-use mid-turn + structured-JSON actions |
| `libs/integrations/whatsapp/d360_client.py` | 429 | ✅ Implementato — client 360dialog con retry/rate-limit |
| `libs/integrations/whatsapp/webhook.py` | — | ✅ Implementato — parser payload inbound |

**Gap:** nessuno.

---

### UC-02 — Booking Autonomo

**Capitolato:** bot prenota appuntamento su GHL Calendar, gestisce proposta slot e slot occupato.

**ISTRUZIONI.md §5.2:**
- Scenario principale con verifica GHL (Calendar, Contacts, Opportunities) + SQL (`appointments`, `analytics_events`).
- Sotto-test proposta slot, sotto-test slot occupato, sotto-test reminder (job `send_appointment_reminders` ogni 30 min).
- Traccia B: Playground mostra evento `book_slot` simulato, `booked=true`.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `libs/ai_core/src/ai_core/actions/booking.py` | 633 | ✅ Implementato — upsert contact, scelta calendario, prenotazione, 3 alternative fallback |
| `libs/integrations/ghl/client.py` | 429 | ✅ Implementato — `get_free_slots`, `create_booking`, `reschedule_appointment`, `cancel_appointment`, `list_calendars` |
| `libs/ai_core/src/ai_core/actions/appointment_change.py` | — | ✅ Implementato — reschedule/cancel |

**Gap:** nessuno. Nota: il job `send_appointment_reminders` è registrato in WorkerSettings.

---

### UC-03 — Gestione Senza Risposta

**Capitolato:** follow-up automatici se lead non risponde; gestione chiamata fallita via GHL event.

**ISTRUZIONI.md §5.3:**
- Parte (a) silenzio in chat: abbassa soglia a 30 min, forza `followup_no_answer` (Appendice B).
- Parte (b) chiamata fallita: webhook GHL `OutboundCall` → `meta.origin='call_failed'`; snippet workaround senza webhook in Appendice B.
- Verifica SQL: `conversations.meta` con `reminders_sent`, `last_reminder_at`, `origin`.
- Nota finestra 24h: testo libero entro 24h, template `no_answer` oltre.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `workers/scheduler/no_answer.py` | 226 | ✅ Implementato — scan idle, config cascade soglie, dedup Redis, invio WA |
| `workers/settings.py` cron | — | ✅ Registrato — ogni 15 min |

**Gap:** nessuno. La parte (b) richiede webhook GHL correttamente configurato (limitazione documentata in §12).

---

### UC-04 — Spostamento Pipeline Automatizzato

**Capitolato:** bot sposta opportunità su GHL quando lead è qualificato; aggiunge nota contatto.

**ISTRUZIONI.md §5.4:**
- Prerequisito: UC-02 eseguito (opportunità esistente in `leads.meta`).
- Verifica GHL: opportunità a `qualified_stage_id` + nota `[Reloop AI] Lead spostato…`.
- SQL: `leads.pipeline_stage_id` + evento `pipeline.moved` con `variant_id`.
- Traccia B: evento `move_pipeline` in Playground.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `libs/ai_core/src/ai_core/actions/pipeline.py` | 316 | ✅ Implementato — upsert contact+opportunity, `move_opportunity`, persistenza stage, analytics |
| `libs/integrations/ghl/client.py` | — | ✅ Implementato — `create_opportunity`, `move_opportunity`, `search_opportunities_by_contact` |

**Gap:** custom field GHL rimandati (limitazione in §12 — dati viaggiano in nota, non in campi custom).

---

### UC-05 — Qualificazione Predittiva con Lead Scoring

**Capitolato:** scoring rules-based turn-by-turn cumulativo; hot ≥80, cold ≤30.

**ISTRUZIONI.md §5.5:**
- Sequenza 3-4 turni (nome, email, budget, booking) con verifica score crescente.
- Testo neutro (`ok`) non fa crollare lo score (segnali cumulativi).
- SQL: `leads.score`, `score_reasons`, `meta.content_signals`.
- UI: grafico "Distribuzione score lead" in Dashboard.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `libs/ai_core/src/ai_core/scoring.py` | 103 | ✅ Implementato — 13 segnali pesati (has_budget +20, asked_for_booking +20, profanity -30, ecc.) |
| `libs/ai_core/src/ai_core/actions/scoring.py` | — | ✅ Implementato — `UpdateScoreHandler`, merge content signals, persistenza cumulativa |
| `libs/ai_core/src/ai_core/sentiment.py` | — | ✅ Implementato — `SentimentAnalyzer` → `lead.sentiment` |

**Gap:** nessuno.

---

### UC-06 — Riattivazione Database Dormiente

**Capitolato:** sequenze automatiche verso lead inattivi ≥90 giorni; opt-out STOP.

**ISTRUZIONI.md §5.6:**
- Test riattivazione: backdate SQL → forza job → verifica testo e `leads.meta.reactivation_attempts`.
- Test opt-out: STOP → `leads.opted_out_at` valorizzato, lead escluso da riattivazioni.
- Note finestra 24h: template `reactivation` richiesto oltre 24h (job salta pulito).

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `workers/scheduler/reactivation.py` | 242 | ✅ Implementato — 3 testi IT per tentativo, config cascade, dedup Redis TTL 14gg |
| `workers/settings.py` cron | — | ✅ Registrato — daily 09:00 |

**Gap:** nessuno.

---

### UC-07 — Knowledge Base

**Capitolato:** upload PDF/DOCX/URL → indexing pgvector → RAG retrieval nel bot.

**ISTRUZIONI.md §5.7:**
- Upload → stato `indexed (N chunk)` entro 30s.
- Test RAG nel Playground (retrieved_chunks visibili).
- SQL: `knowledge_base_docs.status='indexed'` + `kb_chunks`.
- Re-indicizza ed elimina verificati.

**Codebase:**
| File | Righe | Stato |
|---|---|---|
| `libs/ai_core/src/ai_core/rag/indexer.py` | — | ✅ Implementato — parsing PDF/DOCX/URL/txt, chunking, embed, upsert pgvector |
| `libs/ai_core/src/ai_core/rag/retriever.py` | — | ✅ Implementato — `RAGEngine.retrieve()` con cosine distance, min_score, top_k |
| `services/api/src/api/routers/knowledge_base.py` | 214 | ✅ Implementato — upload proxy 20MB, enqueue reindex, list/delete |
| `frontend/apps/web-merchant/src/app/(app)/bot/knowledge-base/page.tsx` | — | ✅ Implementato — `KnowledgeBaseUploader` (273 righe) + `KnowledgeBaseDocList` (189 righe) |

**Gap:** nessuno.

---

### UC-08 — Playground e Addestramento

**Capitolato:** simulazione fedele del flusso reale; editing regole; correzioni bot.

**ISTRUZIONI.md §5.8:**
- Risposta identica al reale, pannello "Stato lead simulato" (score, booked, ecc.).
- Regole: aggiungi → si applica subito → "Salva regole" → persistente.
- Correzioni (§9.2): Modifica → Salva correzione → verifica su messaggio simile.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/playground.py` | ✅ Implementato — `POST /turn` (dry-run), `POST /apply` (regole) |
| `libs/ai_core/src/ai_core/playground.py` | ✅ Implementato — `PlaygroundRunner` stateless, stesso system prompt del live |
| `libs/ai_core/src/ai_core/playground_sim.py` | ✅ Implementato — simulazione pura action senza IO |
| `services/api/src/api/routers/catalog.py` | ✅ Implementato — `/catalog/{merchant_id}/corrections` GET/POST/PATCH/DELETE |
| `libs/ai_core/src/ai_core/corrections.py` | ✅ Implementato — word-overlap scoring, injection nel system prompt |
| `frontend/apps/web-merchant/src/app/(app)/bot/playground/page.tsx` | ✅ Implementato |

**Gap:** matching correzioni euristico (word-overlap) → limitazione documentata in §12; non è un'API vettoriale.

---

### UC-09 — A/B Testing Bot

**Capitolato:** esperimenti con due prompt/config, assegnazione deterministico-sticky, metriche per variante.

**ISTRUZIONI.md §5.9:**
- Crea esperimento, avvia, 10+ conversazioni.
- Metriche con banner significatività (z-test).
- SQL stickiness: stesso lead → stessa variante; eventi con `variant_id` corretto.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/ab_test.py` | ✅ Implementato — list, create, start, stop, metrics con `AbPrimaryMetric` enum |
| `libs/ai_core/src/ai_core/ab_stats.py` | ✅ Implementato — `evaluate_significance` (chi-quadro) |
| `libs/db/src/db/models/ab.py` | ✅ Implementato — `ABExperiment`, `ABAssignment` con RLS |
| `frontend/apps/web-merchant/src/app/(app)/bot/ab-testing/page.tsx` | ✅ Implementato |

**Gap:** nessuno.

---

### UC-10 — Bot Default Agenzia (Template)

**Capitolato:** template default agenzia con lock per i merchant; cascade a tre livelli.

**ISTRUZIONI.md §5.10:**
- Crea template con `rag.top_k=7` + lock → verifica su merchant via `GET /bot-config/<merchant_id>/resolved`.
- Merchant trova campo bloccato (`locked_keys_skipped` sulla PUT).
- Test elimina template.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/bot_config.py` | ✅ Implementato — CRUD templates, `locked_keys`, `is_default`, cascade via `ConfigResolver` |
| `libs/config_resolver/src/config_resolver/` | ✅ Implementato — cascade tre livelli merchant→agency→system con Redis TTL 60s |
| `frontend/apps/web-admin/src/app/(app)/templates/page.tsx` | ✅ Implementato — `<TemplatesPanel />` con UI per-campo + badge Inherited/Customized/Locked |

**Gap:** nessuno.

---

### UC-11 — Dashboard Analytics Merchant

**Capitolato:** KPI merchant in tempo reale (lead, booking, score distribution); filtri periodo e campagna.

**ISTRUZIONI.md §5.11:**
- KPI "Lead totali", "Lead hot", "Tasso risposta", "Booking rate", grafico distribuzione score.
- Filtri: periodo + campagna.
- Verifica: numeri salgono in tempo reale dopo traffico UC-01/02/05.
- SQL A.11.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/analytics.py` | ✅ Implementato — `GET /analytics/kpis` (MerchantKpisOut), `GET /analytics/campaigns`, `POST /analytics/export` |
| `frontend/apps/web-merchant/src/app/(app)/dashboard/page.tsx` | ✅ Implementato — `<MerchantDashboard />` con Supabase Realtime |

**Gap:** Export CSV (§12): backend pronto, pulsante UI non ancora cablato.

---

### UC-12 — Dashboard Unificata Admin Agenzia

**Capitolato:** KPI aggregati cross-merchant; ranking merchant per conversione; drill-down.

**ISTRUZIONI.md §5.12:**
- KPI "Lead totali", "Merchant attivi", "Messaggi ricevuti", "Booking creati" + ranking cliccabile.
- Verifica: totali combaciano con somma merchant (A.12).
- Nota: Export CSV backend pronto, pulsante UI parziale.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/analytics.py` | ✅ Implementato — `GET /analytics/agency` (AgencyKpisOut: leads_total, active_merchants, merchants_ranking) |
| `frontend/apps/web-admin/src/app/(app)/dashboard/page.tsx` | ✅ Implementato — `<AgencyDashboard />` |

**Gap:** Export CSV UI parziale (stesso di UC-11).

---

### UC-13 — Report Obiezioni e Insight

**Capitolato:** estrazione obiezioni da conversazioni; categorizzazione; heatmap e citazioni.

**ISTRUZIONI.md §5.13:**
- Conversazione con 5 tipi di obiezioni → estrazione automatica (cron `close_idle_conversations`) o on-demand.
- UI: grafico barre + heatmap + citazioni; filtri periodo e variante A/B.
- SQL A.13: `objections` con category, quote, severity.
- Vista agenzia aggregata: `GET /reports/objections/agency`.
- Categorie documentate verbatim in Appendice C.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/reports.py` | ✅ Implementato — 3 route: merchant, agency (RBAC), on-demand extraction |
| `libs/ai_core/src/ai_core/objections.py` | ✅ Implementato — `classify_objections` con 7 categorie default |
| `workers/scheduler/objections.py` | ✅ Implementato — `extract_for_conversation`, invocato on-close o daily sweep |
| `frontend/apps/web-merchant/src/app/(app)/reports/objections/page.tsx` | ✅ Implementato — `<ObjectionReport />` |

**Gap:** nessuno.

---

## 2. Feature extra documentate in ISTRUZIONI.md (non nel capitolato V1)

Queste feature sono state sviluppate in corso d'opera e sono correttamente documentate in ISTRUZIONI.md anche se non elencate come UC nel capitolato.

### §6 — Lavagnetta / Automazioni

**Documentazione ISTRUZIONI.md:** editor visuale a grafo, 4 flussi di sistema, automazioni personalizzate event-driven, trigger/condizioni/azioni palette, validazione al salvataggio, engine worker, dedup 24h, anti-loop AI.

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/automations.py` | ✅ Implementato — CRUD grafo, validazione al save |
| `libs/ai_core/src/ai_core/automations.py` | ✅ Implementato — `validate_graph`, `evaluate_condition` |
| `workers/automation/engine.py` | ✅ Implementato — `automation_dispatch` + `automation_run` ARQ |
| `workers/automation/lifecycle.py` | ✅ Implementato |
| `libs/db/src/db/models/automation.py` | ✅ Implementato — 5 trigger, 6 condition types, action types |
| `frontend/apps/web-merchant/src/app/(app)/automazioni/page.tsx` | ✅ Implementato |

**Stato:** ✅ completo e testato in ISTRUZIONI.md.

---

### §7 — Agente tool-use loop (anti-falsa-conferma) + consegna «umana»

**Documentazione ISTRUZIONI.md:** loop `check_availability`/`lookup_appointment` mid-turn, fail-safe AI, staleness inbound, debounce/typing/multi-bolla, throttler 360dialog.

**Codebase:**
| Componente | File | Stato |
|---|---|---|
| Loop tool-use | `libs/ai_core/src/ai_core/orchestrator.py` | ✅ Implementato — multi-iterazione con `ToolExecutor`, feature flag `agent.tool_use_enabled` |
| Fail-safe | `libs/ai_core/src/ai_core/conversation_service.py` | ✅ Implementato — cortesia + handoff su errore LLM |
| Staleness | `workers/conversation/handlers.py` | ✅ Implementato — `inbound_staleness_min` |
| Debounce/consegna | `workers/conversation/handlers.py` | ✅ Implementato — buffering, typing, multi-bolla |
| Throttler 360dialog | `libs/integrations/whatsapp/d360_client.py` | ✅ Implementato — `Retry-After` rispettato |

**Stato:** ✅ completo e testato in ISTRUZIONI.md.

---

### §8 — Template WhatsApp

**Documentazione ISTRUZIONI.md:** UI editor con anteprima bolla live, ciclo Bozza→Approvato, validatore Meta con tabella di 13+ regole, persistenza esempi (migrazione 0026).

**Codebase:**
| File | Stato |
|---|---|
| `services/api/src/api/routers/whatsapp_templates.py` | ✅ Implementato — create (lint+submit 360dialog), list, sync status |
| `workers/scheduler/template_sync.py` | ✅ Implementato — sync cron |
| `libs/db/src/db/models/whatsapp_template.py` | ✅ Implementato |
| `frontend/apps/web-merchant/src/app/(app)/whatsapp-templates/page.tsx` | ✅ Implementato |

**Stato:** ✅ completo e testato in ISTRUZIONI.md.

---

### §9 — Handoff & Correzioni del bot

**Documentazione ISTRUZIONI.md:** pausa 2h, takeover manuale, soft-pause con ripresa automatica, correzioni playground (CRUD, word-overlap).

**Codebase:**
| Feature | File | Stato |
|---|---|---|
| AI pause/resume | `services/api/src/api/routers/conversations.py` | ✅ `POST /conversations/{id}/ai-pause`, `/ai-resume` |
| Auto-takeover | `services/api/src/api/routers/conversations.py` | ✅ `handoff_at` su send_message umano |
| Correzioni | `services/api/src/api/routers/catalog.py` | ✅ `/catalog/{merchant_id}/corrections` CRUD |
| Matching | `libs/ai_core/src/ai_core/corrections.py` | ✅ word-overlap scoring, injection system prompt |

**Stato:** ✅ completo e testato in ISTRUZIONI.md.

---

## 3. Gap e limitazioni residue

### 3.1 Fuori scope ISTRUZIONI.md (non bug)

| Item | Note |
|---|---|
| **Fine-tuning (Fase 3)** | Implementato (`workers/fine_tuning/`, `libs/ai_core/ft/`, FtModelResolver, presidio NER) ma **esplicitamente escluso** da §12: "fuori dallo scope di questi test (fase separata)". Da aggiungere a ISTRUZIONI.md quando si testa la pipeline FT. |
| **FT model routing nel live** | `FtModelProvider` implementato ma non ancora cablato nei call-site della conversazione (come da CLAUDE.md "Deviations"). |
| **DSAR / right-to-erasure** | Menzionato nel capitolato §12 (GDPR), non testato in ISTRUZIONI.md. L'endpoint backend `enforce_retention` esiste ma non c'è un test utente. |

### 3.2 Bug / lacune UI — RISOLTI (2026-06-25)

| Item | UC | Stato |
|---|---|---|
| **Export CSV** pulsante UI | UC-11, UC-12 | ✅ Cablato in `MerchantDashboard` e `AgencyDashboard`: POST `/analytics/exports` → polling → open signed URL. |
| **Stale directories frontend** | — | ✅ Rimosse (erano vuote): `app/bot/`, `app/conversations/`, `app/integrations/`, `app/settings/` in web-merchant; `app/billing/`, `app/merchants/`, `app/settings/`, `app/templates/` in web-admin. |

### 3.3 CI — VERDE (verificato 2026-06-25)

| Check | Stato |
|---|---|
| `ruff check` backend | ✅ 0 errori |
| `ruff format --check` backend | ✅ 275 file già formattati |
| `mypy` backend | ✅ 0 issue in 161 file |
| `pnpm test` frontend | ✅ 3 test passati (`@reloop/supabase-client`); web-admin e web-merchant escono con code 0 |
| `pnpm typecheck` frontend | ✅ 7/7 package |
| `pnpm lint` frontend | ✅ 0 warning |

### 3.4 Deviazioni capitolato non aggiornate nel capitolato stesso

| Deviazione | Capitolato | Realtà (ISTRUZIONI.md + codebase) |
|---|---|---|
| WhatsApp provider | "Meta Cloud API — BSP diretto" | 360dialog con `d360_client.py` + BSP router layer |
| Webhook GHL | `POST /webhooks/ghl/{merchant_id}` | Endpoint unificato `POST /webhooks/ghl/marketplace` (INSTALL + dati) |
| Modelli AI | gpt-5-mini, gpt-5-nano, gpt-5.2 | Nomi nel capitolato (spec di budget); i nomi reali sono nel codice (`ModelRouter`) |
| Webhook signature GHL | HMAC-SHA256 | Ed25519 (`x-ghl-signature`) preferito + RSA-SHA256 legacy |

Queste deviazioni sono già documentate in `CLAUDE.md` e `docs/decisions/`. ISTRUZIONI.md le riflette correttamente.

---

## 4. Verifica ISTRUZIONI.md vs capitolato: elementi mancanti o non allineati

| Punto | ISTRUZIONI.md | Note |
|---|---|---|
| **Mappa UC→componenti** (capitolato §10) | Non ripetuta (corretta: è redundante nella guida di test) | OK |
| **Gerarchia config §9.4** (20 parametri) | Documentata in Appendice C (subset) + §3.7 + §6.6 | I parametri `agent.*` e `delivery.*` sono in Appendice C; i 20 del capitolato sono coperti |
| **Pattern UI Inherited/Customized/Locked** (capitolato §9.5) | Documentato in §6.6 e §5.10 | OK |
| **Guardrails §6.8** (token budget, content filter, fallback) | Parzialmente: il fail-safe §7.2 copre il fallback; content filter non testato esplicitamente | Minore — non critico per il collaudo |
| **Backup/DR §13.6** | Non presente in ISTRUZIONI.md | Corretto: è un test operativo, non utente |
| **Isolamento RLS** | Presente in §10.1 con comandi `curl` | ✅ |
| **Firma webhook GHL** | Presente in §10.2 | ✅ |

---

## 5. Conclusione

**Tutti i 13 UC del capitolato sono:**
1. **Definiti** nel capitolato `reloop-ai-architettura.md`
2. **Testati** in `ISTRUZIONI.md` con scenario, passi oggettivi, verifica GHL/SQL/UI
3. **Implementati** in codebase con file reali e non-stub

**Le feature extra** (Lavagnetta, Tool-use, Template WA, Handoff/Correzioni) sono un superset rispetto al capitolato V1, correttamente documentate in ISTRUZIONI.md §6-9.

**Azioni fatte (2026-06-25):**
1. ✅ **CI:** ruff/mypy/vitest/typecheck/lint tutti verdi — nessun intervento necessario (erano già verdi).
2. ✅ **Export CSV:** pulsante UI cablato in `MerchantDashboard` (web-merchant) e `AgencyDashboard` (web-admin).
3. ✅ **Stale dirs:** rimosse tutte le cartelle vuote fuori dal route group `(app)/`.

**Azioni aperte (fuori scope di questo intervento):**
- **Fine-tuning:** quando la pipeline FT viene testata, aggiungere §10 in ISTRUZIONI.md.
- **DSAR:** aggiungere un test esplicito per right-to-erasure (endpoint `enforce_retention`).

---

_Audit eseguito il 2026-06-25. Sostituisce `uc-gap-audit-2026-06-17.md` come riferimento corrente._
