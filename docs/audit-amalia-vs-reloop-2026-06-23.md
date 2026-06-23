# Audit: Amalia vs ReloopxWave — Agente AI + Impostazioni WhatsApp

**Data:** 2026-06-23

Confronto ingegneristico tra **Amalia** (riferimento che funziona bene in produzione) e **ReloopxWave** (da rendere piu' funzionante), focalizzato su agente AI e canale WhatsApp. Entrambi backend Python. Obiettivo: identificare dove ReloopxWave deve avvicinarsi ad Amalia, dove e' gia' superiore, e produrre una roadmap di interventi prioritizzata.

---

## Executive summary

- **Tesi centrale — il paradigma dell'agente e' la differenza piu' impattante.** Amalia esegue un VERO loop agentico tool-use nativo Anthropic (max 5 iterazioni): il modello chiama un tool, VEDE il risultato reale (inventario, slot, ordine) reiniettato come `tool_result`, ragiona e adatta la risposta nello stesso turno. ReloopxWave usa "structured-JSON actions" single-shot: UNA chiamata LLM emette `{reply_text, actions[]}`, le azioni girano DOPO la reply come effetti collaterali best-effort, e il modello non vede mai l'esito. Conseguenza: ReloopxWave puo' dire "ti ho prenotato per le 15" anche quando lo slot e' occupato o GHL non e' configurato.
- **Divergenza testo<->azione = bug di qualita' percepita.** Amalia ha guardie anti-allucinazione (es. guardia COD) e read-back; ReloopxWave swallowa le eccezioni degli handler e invia la conferma "alla cieca". E' la classe di errori piu' dannosa per un agente di vendita/booking su WhatsApp.
- **Contesto cliente nel prompt.** Amalia inietta nome/storico ordini/spesa/tag e fa riferimento al passato ("Ciao Marco, riguardo al tuo ordine di ieri..."); ReloopxWave inietta SOLO il punteggio lead numerico, partendo ogni turno semi-cieco e ripetendo domande gia' risposte.
- **Canale WhatsApp — ReloopxWave manca di throttling.** Amalia tratta il rate limiting come prima classe (per-provider, finestra 60s, min_gap); ReloopxWave non ha alcun throttling in uscita: su campagne/multi-bubble rischia 429 a raffica e degrado della quality rating Meta. ReloopxWave e' pero' superiore su finestra-24h (enforcement runtime), linter template e typing/read-receipt reali.
- **Bug cross-tenant concreto.** `apply_template_status_event` chiama `apply_status_by_name(merchant_id=None)`: un evento Meta su un nome template comune (es. "reminder") aggiorna le righe di TUTTI i merchant, violando l'isolamento RLS del progetto.
- **Timing umano — capability migliore ma spenta.** ReloopxWave ha debounce su worker, typing, multi-bolla, jitter deterministico (tecnicamente superiori ad Amalia) ma TUTTI a default no-op: out-of-the-box sembra un bot. Amalia ha il comportamento umano hardcoded e sempre attivo. Manca inoltre a ReloopxWave uno staleness check (rischio risposte tardive fuori contesto dopo backlog).
- **Fail-safe.** Amalia garantisce SEMPRE una risposta al cliente (`handle_ai_conversation_safe`); ReloopxWave su doppio fallimento LLM puo' restare muto.
- **Dove ReloopxWave vince:** cascata config con badge Inherited/Customized/Locked, persona strutturata ortogonale, scoring sempre-attivo deterministico, durabilita' persist/generate separata, automazioni a nodi, linter template, enforcement 24h. Da preservare e valorizzare.

---

## Tabella diff per dimensione

| Dimensione | Approccio Amalia | Approccio ReloopxWave | Impatto |
|---|---|---|---|
| **1. Loop ragionamento + strategia modello** | Loop tool-use nativo (max 5 iter, Haiku 4.5), tool-result reiniettato, 14 tool, guardie anti-allucinazione, safe-wrapper | Single-shot structured-JSON, 8 ActionKind dispatchati dopo la reply, ModelRouter (gpt-5-mini/nano/5.2 + FT + fallback Anthropic OFF) | Amalia grounded sui dati veri -> piu' affidabile; ReloopxWave deterministico su costo/latenza ma promette cio' che non verifica |
| **2. Prompt engineering / persona / knowledge** | Builder 13 blocchi a priorita' esplicita, contesto cliente inline, FAQ inline, policy on-demand, rinforzo anti-drift | Builder cascata (parita' playground), persona strutturata ricca, correzioni playground, policy inline; solo score nel prompt | Amalia piu' contestuale/grounded e con gerarchia; ReloopxWave piu' granulare sulla persona ma cieco sul cliente |
| **3. Copertura ed esecuzione tool/azioni** | Tool-calling nativo + ReAct, 14 tool (catalogo/ordini/COD/policy/handoff), filtro per capability, input_schema validato | 8 azioni quasi tutte di scrittura (booking/pipeline/score/escalate), no read on-demand, payload dict libero, registry pulita | Amalia copre lookup/transazioni e ragiona sull'esito; ReloopxWave ha scoring deterministico migliore ma copertura conversazionale limitata |
| **4. WhatsApp (invio/template/webhook/provider)** | Multi-BSP (Protocol + 2 adapter), rate limit prima classe, outbox+retry, sanitizzazione, media in ingresso | Single-BSP 360dialog, enforcement 24h runtime, linter template ricco, typing/read reali; NO throttling, NO sanitize, bug cross-tenant template-status | ReloopxWave migliore su 24h/template/typing ma rischioso su deliverability (429) e con un bug di isolamento reale |
| **5. Timing / accumulo / consegna umana / follow-up** | Comportamento umano hardcoded sempre-on, staleness 5min, accumulo 3s, safe-wrapper | Delivery puro deterministico, debounce su worker, durabilita' separata; tutti i knob a default no-op, no staleness, no cap debounce | Amalia sembra umano out-of-the-box; ReloopxWave ha capability superiori ma spente + rischio risposte tardive |
| **6. Config merchant + UX** | Console unica AI Engine, sezioni guidate, capacita' a Switch, correzioni dal Playground | Config su 7 pagine, cascata trasparente, persona cablata, editor template/automazioni superiori; 7 sezioni nascoste, settings vuoto | Amalia piu' comprensibile e completa percepita; ReloopxWave piu' capace ma appare immaturo e frammentato |

---

## Dettaglio per dimensione

### 1. Loop di ragionamento dell'agente e strategia modello

- **Loop iterativo vs single-shot** — *vincitore: Amalia.* Amalia chiama un tool, vede il risultato, ragiona e risponde nello stesso turno; ReloopxWave emette azioni "alla cieca" e gli handler girano dopo l'invio. Determinante per booking/vendita: il modello puo' verificare disponibilita' reale ed esito prenotazione, evitando di promettere cio' che il sistema non realizza.
- **Tool di LETTURA on-demand** — *vincitore: Amalia.* 14 tool molti di lettura (check_inventory live, get_order_details). ReloopxWave non ha alcun lookup invocabile: o e' nel prompt o non c'e'. Limita la copertura conversazionale ("lo slot delle 15 e' libero?", "a che punto e' il mio ordine?").
- **Riconciliazione testo<->azione (anti-allucinazione)** — *vincitore: Amalia.* Guardia COD: se il modello dice "ordine confermato" ma il tool non e' stato eseguito, il testo viene soppresso. ReloopxWave puo' fallire silenziosamente dopo che la reply ha gia' detto "fatto".
- **Numero chiamate LLM / costo-latenza** — *vincitore: ReloopxWave.* 1 chiamata principale (+1 sentiment nano) deterministica; Amalia 1-5 chiamate (loop) con blocking sleep 3s in sessione DB (rischio pool). ReloopxWave piu' prevedibile.
- **Gestione errori tool** — *vincitore: Amalia.* `ToolResult` uniforme {success,error} torna al modello che reagisce; in ReloopxWave l'errore diventa silenzio.
- **Accumulo/debounce** — *vincitore: ReloopxWave.* Debounce in `delivery.py` senza sleep in sessione, design piu' sano per il pooling.
- **Durabilita' persist/generate** — *vincitore: ReloopxWave.* Fase 1 persist sincrona+idempotente su `wa_message_id`, generazione separata.
- **Filtro azioni per merchant** — *vincitore: Amalia.* `get_filtered_tools` applica i toggle al prompt e ai tool; in ReloopxWave `allowed_actions` esiste solo per le automazioni.
- **Vincolo temperature GPT-5** — *vincitore: Amalia.* Haiku rispetta temp 0.4; GPT-5 ignora la temperature (locked a 1), scartando le 0.1/0.3 volute.
- **Fallback provider e response_format** — *pari.* Amalia non ha ridondanza provider; ReloopxWave ce l'ha ma rotta (no JSON in fallback) e OFF di default.
- **Garanzia risposta sempre** — *vincitore: Amalia.* Safe-wrapper invia sempre cortesia + needs_human; ReloopxWave puo' restare muto su errore.

### 2. Prompt engineering, persona e iniezione conoscenza

- **Gerarchia di priorita' esplicita** — *vincitore: Amalia.* Blocchi "REGOLE FERREE - PRIORITA' ASSOLUTA"; in ReloopxWave solo le correzioni hanno priorita' dichiarata.
- **Contesto runtime cliente/lead** — *vincitore: Amalia.* Nome, ordini, spesa, tag, ultimo spedito; ReloopxWave inietta solo il punteggio numerico. E' il driver #1 della naturalezza percepita.
- **Tool execution vs single-shot** — *vincitore: Amalia.* Grounded sui dati veri; ReloopxWave deduce le azioni dal solo messaggio.
- **Policy inline vs on-demand** — *vincitore: ReloopxWave.* Policy sempre inline garantite nel contesto (piu' robusto per Q&A policy); Amalia risparmia token ma rischia policy mancanti.
- **FAQ injection** — *vincitore: Amalia.* FAQ inline canoniche sempre presenti; in ReloopxWave le Q&A passano solo per RAG (min_score 0.7) che puo' mancare il match su domande banali.
- **Pre-filtraggio correzioni** — *pari.* Logica identica (overlap lessicale, soglia 0.4, top 2): ReloopxWave l'ha portata da Amalia. Stesso limite (niente semantica).
- **Rinforzo anti-drift in coda** — *vincitore: Amalia.* Sezione finale "ignora lo stile precedente, non cambiare nome"; ReloopxWave esposto a drift su conversazioni lunghe.
- **Persona strutturata deterministica** — *vincitore: ReloopxWave.* Formality + verbosity + emoji_policy + greeting + signature + do/dont + few-shot, ortogonali e snapshot-testabili.
- **Allineamento capacita'<->tool** — *vincitore: Amalia.* I flag can_* gatano prompt e tool; ReloopxWave dichiara sempre tutte le azioni anche se l'handler non e' configurato.
- **Adattamento al sentiment** — *vincitore: ReloopxWave.* Frammento empatia/upsell dal sentiment del turno precedente, zero latenza aggiunta.
- **Temperature efficace** — *vincitore: Amalia.* GPT-5 scarta la temperature.
- **A/B prompt e perdita personalizzazione** — *vincitore: Amalia.* `PromptManager` sostituisce l'INTERO prompt, bypassando correzioni e sentiment proprio mentre si misura.

### 3. Copertura ed esecuzione tool/azioni dell'agente AI

- **Meccanismo esecuzione (tool-calling+ReAct vs JSON one-shot)** — *vincitore: Amalia.* Senza read-back il bot non sa se l'azione e' riuscita quando compone la frase. E' la differenza tra "agente" e "classificatore di intenti".
- **Tool di lettura on-demand** — *vincitore: Amalia.* get_customer_orders/check_inventory; ReloopxWave nessuno (RAG precomputato, get_free_slots nascosto).
- **Copertura catalogo prodotti** — *vincitore: Amalia.* Gap solo in ottica e-commerce; per il dominio lead-gen attuale non e' funzionale ma limita l'espansione.
- **Ordini/pagamenti/checkout** — *vincitore: Amalia.* Idem: leva da closer transazionale.
- **Scrittura CRM libera (tag/nota)** — *vincitore: Amalia.* In ReloopxWave `add_contact_note` e' usato solo internamente; il bot non puo' taggare interessi/annotare obiezioni in chat.
- **Disabilitazione granulare tool nel live** — *vincitore: Amalia.* Toggle simmetrici; ReloopxWave ha `allowed_actions` solo nelle automazioni.
- **Registry vs dispatcher manuale** — *vincitore: ReloopxWave.* `ActionDispatcher.register` piu' pulito dell'if/elif.
- **Robustezza scoring / anti-allucinazione** — *vincitore: ReloopxWave.* `derive_signals_from_llm_payload` whitelista + update_score server-side sempre-attivo: qualificazione lead piu' solida.
- **Sicurezza azioni distruttive** — *pari.* Amalia su ordini (anti-duplicato), ReloopxWave su appuntamenti (disambiguazione, code-path unico).
- **Validazione tipizzata payload** — *vincitore: Amalia.* input_schema validato dal provider; in ReloopxWave `payload: dict[str,Any]` degrada silenziosamente.
- **Coerenza proposta->prenotazione slot** — *vincitore: Amalia.* check live prima dell'azione; `propose_slots` non registra lo slot proposto, `book_slot` si fida del `preferred_start_iso` del LLM.

### 4. Impostazioni WhatsApp

- **Astrazione multi-BSP** — *vincitore: Amalia.* Protocol + 2 adapter; ReloopxWave ha l'astrazione ma un solo adapter.
- **Rate limiting in uscita** — *vincitore: Amalia.* ASSENTE in ReloopxWave: rischio 429 a raffica e degrado quality rating Meta.
- **Outbox + retry + gate** — *vincitore: Amalia.* Drain batch con SKIP LOCKED e backoff a stati; ReloopxWave ha solo retry client (tenacity).
- **Sanitizzazione testo outbound** — *vincitore: Amalia.* Due livelli + golden vectors anti-Meta-#100; ReloopxWave nessuna sanitize ne' troncamento 4096.
- **Normalizzazione numero E.164** — *vincitore: Amalia.* `phonenumbers` e' tra le deps di ReloopxWave ma non usato.
- **Finestra 24h** — *vincitore: ReloopxWave.* Enforcement RUNTIME in due punti con policy e skip pulito; Amalia per convenzione.
- **Selezione/validazione template** — *vincitore: ReloopxWave.* Linter pre-submit superiore.
- **Typing / read receipt in uscita** — *vincitore: ReloopxWave.* status:read + typing_indicator in una call; Amalia probabile no-op su 360dialog.
- **Parsing webhook** — *pari.* Entrambi robusti; ReloopxWave parser puri + firma sempre presente, Amalia copre piu' provider.
- **Media in ingresso** — *vincitore: Amalia.* Download/trascrizione; ReloopxWave solo segnaposto.
- **Scoping multi-tenant template-status** — *vincitore: Amalia.* Bug reale in ReloopxWave: `merchant_id=None` propaga cross-tenant.
- **Idempotenza inbound** — *vincitore: Amalia.* ON CONFLICT DO NOTHING; ReloopxWave mitigato via job_id ARQ.

### 5. Timing, accumulo, consegna umana, follow-up

- **Default comportamento umano** — *vincitore: Amalia.* Sempre-on hardcoded; ReloopxWave tutto opt-in no-op -> percezione robotica out-of-the-box.
- **Accumulo messaggi rapidi** — *pari.* Meccanismo ReloopxWave tecnicamente migliore (no DB-block, idempotente) ma off di default e senza cap.
- **Staleness (messaggi vecchi/backlog)** — *vincitore: Amalia.* STALENESS 5min; ReloopxWave assente -> dopo downtime risponde a conversazioni vecchie fuori contesto.
- **Typing + ritardo naturale** — *pari.* Implementazione ReloopxWave migliore (no bug del "1s fisso") ma default off.
- **Split in piu' bolle** — *vincitore: ReloopxWave.* `split_into_bubbles` per paragrafo/frase; Amalia bolla unica. Default off.
- **Durabilita' esecuzione turno** — *vincitore: ReloopxWave.* Persist sincrona separata + worker ARQ con dedup; Amalia tutto inline nel BackgroundTask.
- **Garanzia risposta su errore** — *vincitore: Amalia.* safe-wrapper; ReloopxWave puo' restare muto su doppio fallimento.
- **Cap massimo attesa debounce** — *vincitore: Amalia.* ReloopxWave senza max_wait: peer chiacchierone ritarda indefinitamente la prima risposta.
- **Follow-up / lifecycle** — *pari.* ReloopxWave enforcement 24h piu' esplicito; Amalia ancora il timer COD alla consegna reale.
- **Attribuzione wamid nel path coalescente** — *vincitore: Amalia.* In ReloopxWave il wamid "latest" puo' essere impreciso (limite ADR 0008).

### 6. Superficie di configurazione merchant e UX

- **Console unica vs frammentata** — *vincitore: Amalia.* /ai-engine in un colpo; ReloopxWave 7 pagine scollegate senza hub.
- **Sezioni operative nascoste** — *vincitore: Amalia.* no_answer/reactivation/scoring/rag/pipeline/booking nello schema ma in HIDDEN_SECTIONS.
- **Trasparenza ereditarieta'** — *vincitore: ReloopxWave.* Cascata con badge Inherited/Customized/Locked e reset per-campo.
- **Capacita'/tool come Switch** — *vincitore: Amalia.* 6 Switch chiari; ReloopxWave solo per-ai_reply nelle automazioni.
- **Orari operativi UI** — *vincitore: Amalia.* Switch 24/7 + time picker; ReloopxWave free-text error-prone.
- **Editor template WhatsApp** — *pari* (leggero vantaggio ReloopxWave su qualita' linter/anteprima).
- **Automazioni** — *vincitore: ReloopxWave.* Lavagnetta a nodi E/O/NOT vs set fisso di 6 tipi.
- **Correzioni / feedback loop** — *vincitore: Amalia.* Playground alimenta override di prompt; ReloopxWave non ha questa superficie.
- **Esempi few-shot** — *pari.* Nessuna delle due li espone (ReloopxWave li ha nello schema ma non in UI).
- **Auto-reply granularita'** — *vincitore: ReloopxWave.* Doppio livello master + per-thread.
- **Delivery / tono umano** — *vincitore: ReloopxWave.* Capability unica ma esposta in modo troppo tecnico (7 knob senza preset).
- **Settings account/team/export** — *vincitore: Amalia.* /settings ReloopxWave quasi vuoto ("In arrivo").
- **Anteprima prompt nella console** — *vincitore: Amalia.* Playground integrato; ReloopxWave preview in pagina separata.

---

## Roadmap raccomandazioni

Tutte le raccomandazioni, ordinate per priorita' (P0 prima).

| Priorita' | Dimensione | Cosa fare | File da toccare | Effort | Impact |
|---|---|---|---|---|---|
| P0 | Loop agente | Loop tool-use selettivo per azioni con esito (book_slot/propose_slots/lookup): eseguire in-line, reiniettare ToolResult nell'LLM (max 2-3 iter) solo quando servono dati veri | `orchestrator.py`, `conversation_service.py`, `actions/booking.py` | L | alto |
| P0 | Loop agente | Riconciliazione testo<->azione prima dell'invio (porting guardia COD): eseguire book/reschedule/cancel PRIMA del testo, o riscrivere reply in richiesta-conferma | `conversation_service.py`, `actions/booking.py` | M | alto |
| P0 | Loop agente | Fail-safe: garantire sempre una risposta al cliente su errore LLM (porting `handle_ai_conversation_safe`), marcare escalated | `conversation_service.py` | S | alto |
| P0 | Prompt/persona | Iniettare blocco "Profilo cliente / contesto lead" (nome, sentiment, score qualitativo, stage, mini-storico) nel prompt | `orchestrator.py`, `conversation_service.py` | M | alto |
| P0 | Prompt/persona | Gerarchia priorita' esplicita + blocco "Regole ferree" merchant-level (`bot.hard_rules`) in testa al prompt | `config_resolver/schema.py`, `conversation_service.py`, `bot-config-panel.tsx` | M | alto |
| P0 | WhatsApp | Rate limiter/throttler in uscita (Redis sliding-window/token-bucket, per-merchant, rispetta Retry-After sui 429) | `d360_client.py`, `conversation/handlers.py`, `outbound.py`, `config_resolver/schema.py`, `runtime.py` | M | alto |
| P0 | WhatsApp | Fix scoping multi-tenant template-status: risolvere merchant da phone_number_id o usare whatsapp_template_id, NO `merchant_id=None` | `scheduler/template_sync.py`, `repositories/whatsapp_template.py`, `whatsapp/webhook.py` | S | alto |
| P0 | Timing | Staleness check sull'inbound (config `schedule.inbound_staleness_min` ~10min): skip generazione se turno troppo vecchio | `conversation/handlers.py`, `conversation_service.py`, `config_resolver/schema.py`, `webhooks.py` | S | alto |
| P0 | Timing | Preset "human-feel" con default sensati per i knob delivery (debounce, typing, jitter, multi-bubble) | `config_resolver/schema.py`, `docs/decisions/0008-...md` | S | alto |
| P0 | Config/UX | Esporre sezioni nascoste a basso rischio (no_answer/reactivation/scoring/booking) dietro "Avanzate" invece di nasconderle | `bot-config-panel.tsx` | S | alto |
| P1 | Loop agente | Passare response_format json_object (o re-prompt JSON) anche al fallback Anthropic, cosi' le azioni non si perdono | `orchestrator.py`, `llm.py`, `router.py` | M | medio |
| P1 | Loop agente | Aggiungere azioni di LETTURA on-demand: check_availability, lookup_contact, get_appointment_status (riuso ghl/client) | `orchestrator.py`, `runtime.py`, `ghl/client.py` | L | alto |
| P1 | Loop agente | Controllo granulare azioni nel turno live (`AGENT_ALLOWED_ACTIONS` nella cascata, filtro come run_proactive) | `orchestrator.py`, `config_resolver/schema.py`, `conversation_service.py` | S | medio |
| P1 | Prompt/persona | Blocco FAQ deterministico sempre-presente (tabella/campo FAQ, inline prima del RAG) | `conversation_service.py`, `config_resolver/schema.py`, `brand-info-panel.tsx` | M | alto |
| P1 | Prompt/persona | Rinforzo persona anti-drift in coda al prompt (mantieni identita'/nome/regole) | `conversation_service.py` | S | medio |
| P1 | Prompt/persona | Grounding azioni fattuali (booking) prima di promettere, o filtrare schema azioni alle sole configurate (porting get_filtered_tools) | `orchestrator.py`, `conversation_service.py`, `actions/booking.py` | M | alto |
| P1 | Tool/azioni | Reincorporare esito azioni nel turno (mini ReAct a 1 step): ActionOutcome -> seconda chiamata orchestrator con i risultati | `conversation_service.py`, `orchestrator.py`, `actions/booking.py`, `actions/pipeline.py` | L | alto |
| P1 | Tool/azioni | ActionKind di lettura: check_availability + lookup_appointment (GHL get_free_slots / AppointmentRepository) | `orchestrator.py`, `actions/appointment_change.py`, `runtime.py`, `ghl/client.py` | M | alto |
| P1 | Tool/azioni | ActionKind add_note/set_tag per arricchire il CRM in conversazione (riuso add_contact_note + whitelist tag) | `orchestrator.py`, `actions/pipeline.py`, `runtime.py`, `automation/engine.py` | M | medio |
| P1 | Tool/azioni | Toggle per-azione anche nel turno inbound live (`BOT_ALLOWED_ACTIONS`, riflesso nel prompt) | `orchestrator.py`, `conversation_service.py`, `config_resolver/schema.py`, `bot-config-panel.tsx` | M | medio |
| P1 | WhatsApp | Sanitizzare e troncare il testo outbound (strict param / gentle free-text + cap 4096, porting golden vectors) | `d360_client.py`, `whatsapp/templates.py`, `ai_core/delivery.py` | M | alto |
| P1 | WhatsApp | Normalizzare numeri destinatario in E.164 (`phonenumbers`, centralizzato inbound+outbound) | `d360_client.py`, `conversation/handlers.py`, `webhooks.py` | S | medio |
| P1 | Timing | Cap massimo attesa debounce (`debounce.max_wait_s` ~30s da primo frammento, first-seen Redis) | `conversation/handlers.py`, `ai_core/delivery.py`, `config_resolver/schema.py` | S | medio |
| P1 | Timing | Garantire sempre una risposta visibile su doppio fallimento LLM (messaggio cortesia + needs-human) | `conversation_service.py`, `orchestrator.py`, `config_resolver/schema.py` | M | medio |
| P1 | Timing | Consolidare attribuzione wamid nel path debounce per typing/read-receipt (persistere wamid ultimo frammento) | `conversation/handlers.py`, `conversation_service.py`, `d360_client.py` | M | medio |
| P1 | Config/UX | Sezione "Capacita'" (toggle tool) nel bot-config, mappata su schema config dedicato | `bot-config-panel.tsx`, `config_resolver/schema.py`, `conversation_service.py` | M | alto |
| P1 | Config/UX | Sostituire schedule.active_hours free-text con editor a fasce orarie + selettore timezone | `bot-config-panel.tsx`, `conversation_service.py` | M | medio |
| P1 | Config/UX | Collassare i 7+ delivery knob dietro 3 preset (Naturale lento / Bilanciato / Veloce) + "Avanzate" | `bot-config-panel.tsx` | S | medio |
| P1 | Config/UX | Hub/console unico del bot con navigazione interna + checklist onboarding | `bot/config/page.tsx`, `bot-config-panel.tsx` | L | alto |
| P2 | Loop agente | Timeout/cancellazione esplicita sulle LLM call + MAX_TOOL_ITERATIONS con telemetria | `orchestrator.py`, `shared/settings.py`, `llm.py` | S | medio |
| P2 | Loop agente | Allineare taxonomy obiezioni + instradare il classificatore via ModelRouter (purpose=classification) | `objections.py`, `config_resolver/schema.py`, `scheduler/objections.py`, `router.py` | S | basso |
| P2 | Prompt/persona | Preservare correzioni + sentiment anche nelle varianti A/B (append dopo il body autorato) | `prompt_manager.py`, `conversation_service.py` | S | medio |
| P2 | Prompt/persona | Esporre campo few-shot (Q/A) nel pannello persona (bot.examples gia' consumato) | `bot-config-panel.tsx` | S | medio |
| P2 | Prompt/persona | Scoring correzioni semantico via embeddings (riuso Embedder/pgvector, OR-merge con lessicale) | `corrections.py`, `conversation_service.py` | L | medio |
| P2 | Tool/azioni | Validazione tipizzata per-kind dei payload (Pydantic per BookSlot/MovePipeline...), degradare esplicito | `orchestrator.py`, `conversation_service.py`, `actions/booking.py`, `actions/pipeline.py` | M | medio |
| P2 | Tool/azioni | Handle persistente dello slot proposto per coerenza propose->book + validazione live | `actions/booking.py`, `models/conversation.py`, `db/repositories/` | M | medio |
| P2 | Tool/azioni | RAG come tool invocabile (knowledge_lookup on-demand) — dopo il loop ReAct | `orchestrator.py`, `conversation_service.py`, `runtime.py` | L | medio |
| P2 | WhatsApp | Drain outbound unico con backoff a stati (SKIP LOCKED, retry_count, rate-gate), unifica composer+proattivi | `conversation/handlers.py`, `outbound.py`, `settings.py`, `repositories/conversation.py` | L | medio |
| P2 | WhatsApp | Scaricare e processare media in ingresso (download, Storage, OCR/trascrizione) | `d360_client.py`, `webhooks.py`, `conversation/handlers.py`, `supabase_storage.py` | L | medio |
| P2 | WhatsApp | Reagire a segnali qualita'/stato canale Meta (quality_update, account_update, template PAUSED/FLAGGED) | `whatsapp/webhook.py`, `whatsapp/d360_templates.py`, `scheduler/integration_health.py`, `webhooks.py` | M | medio |
| P2 | Timing | Allineare le finestre business-hours dei cron al timezone per-merchant | `scheduler/reactivation.py`, `scheduler/no_answer.py`, `settings.py` | M | basso |
| P2 | Config/UX | Superficie "Correzioni" alimentata dal Playground con override prompt per-merchant | `conversation_service.py`, `routers/bot_config.py`, `bot-config-panel.tsx` | L | medio |
| P2 | Config/UX | Anteprima del prompt risultante (incl. system_prompt_additions) dentro /bot/config | `bot-config-panel.tsx`, `routers/bot_config.py`, `conversation_service.py` | M | medio |
| P2 | Config/UX | Esporre campo few-shot (bot.examples) nella sezione Persona/Avanzate | `bot-config-panel.tsx`, `conversation_service.py` | S | basso |
| P2 | Config/UX | Completare /settings: notifiche, timezone account, export CSV (utenti/team in roadmap) | `settings-panel.tsx` | M | basso |

---

## Quick wins (P0/P1 a basso effort)

Interventi ad alto rapporto valore/effort, attaccabili subito:

1. **[P0, S] Fix bug cross-tenant template-status** — eliminare `apply_status_by_name(merchant_id=None)`: e' un difetto di isolamento reale che viola gli invarianti RLS del progetto. Risolvere il merchant da `phone_number_id` o usare `whatsapp_template_id`.
2. **[P0, S] Fail-safe risposta sempre** — wrappare la generazione in try/except che invia un messaggio di cortesia + escalate su errore LLM. Poche righe, elimina il "silenzio" sul cliente.
3. **[P0, S] Staleness check inbound** — soglia ~10min sull'eta' del messaggio prima di generare, per non rispondere a backlog vecchio fuori contesto.
4. **[P0, S] Preset "human-feel" di default** — spostare i knob delivery da no-op a valori realistici (la capability esiste gia' ed e' superiore ad Amalia: manca solo l'attivazione).
5. **[P0, S] Sbloccare le sezioni nascoste** — ridurre `HIDDEN_SECTIONS` esponendo no_answer/reactivation/scoring/booking sotto "Avanzate": chiude il gap percepito piu' grande senza nuova logica backend.

Altri P1 a basso effort: rinforzo persona anti-drift in coda (S), normalizzazione E.164 con `phonenumbers` gia' tra le deps (S), cap massimo attesa debounce (S), controllo granulare azioni nel turno live (S), 3 preset per i delivery knob (S).
