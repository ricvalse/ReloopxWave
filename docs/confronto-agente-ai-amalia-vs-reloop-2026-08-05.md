# Amalia vs ReloopxWave — confronto del motore agente AI su WhatsApp

*Convenzione di citazione: `A:` = percorsi relativi a `/Users/riccardo/Progetti/Amalia/amalia-ai/`, `R:` = percorsi relativi a `/Users/riccardo/Progetti/ReloopxWave/`. Ogni affermazione è ancorata a file:riga verificata.*

---

## Verdetto in tre righe

**Amalia è un agente, Reloop è un classificatore di intenti con un motore di orchestrazione attorno.** In Amalia il modello chiama tool nativi che mutano lo stato *dentro* il turno, vede l'esito reale e scrive un solo messaggio coerente con quello che è davvero successo (`A:services/backend/app/services/ai_agent/agent.py:194-258`); in Reloop il modello emette un JSON, il testo parte sul filo, e solo *dopo* gli handler tentano booking/CRM/escalation al buio (`R:backend/libs/ai_core/src/ai_core/conversation_service.py:1943`).

**Reloop è invece una piattaforma molto più matura di Amalia su tutto ciò che circonda il turno**: cascata di configurazione a 4 livelli, playbook, profili di conversazione, finestra 24h enforced, handoff exactly-once con SLA, attribuzione per messaggio, A/B + bandit, fine-tuning per tenant, RLS, retention GDPR. Amalia non ha nulla di tutto questo — ha un solo store, un solo modello, un solo prompt.

**Il divario di qualità della risposta è quasi interamente riconducibile a cinque scelte**: (1) tool-use nativo vs JSON simulato, (2) scritture in-turn vs post-turn, (3) guardia deterministica testo↔azione vs solo prompt, (4) parità playground↔produzione per costruzione vs due runner paralleli, (5) parametri di generazione espliciti (temp 0.4, 500 token) vs nessun parametro passato al modello.

---

## Tabella sinottica

| Aspetto | Amalia | Reloop | Chi vince |
|---|---|---|---|
| Protocollo tool-use | `tools=[...]` + `tool_choice:auto` nativo Anthropic, `tool_result` correlati per id (`A:agent.py:194-196,250-258`) | `response_format:json_object`, "tool" = 2 voci dell'enum `ActionKind`, osservazione reiniettata come prosa italiana in un messaggio `user` (`R:orchestrator.py:47,206-241`) | **Amalia** |
| Tool disponibili al modello mid-turn | 14 definiti, 12 attivi di default, filtrati per capability (`A:tools.py:13-384`) | 2 read-tool (`check_availability`, `lookup_appointment`) + 7 azioni post-turno cieche (`R:actions/read_tools.py:54-61`) | **Amalia** |
| Le azioni con effetti girano… | dentro il loop, con commit (`A:tool_executor.py:459,711`) | dopo l'invio della reply (`R:conversation_service.py:1943`) | **Amalia** |
| Chi scrive la conferma dell'esito | il modello, 1 messaggio nel tono configurato (`A:agent.py:524`) | template fisso hardcoded in una **seconda** bolla (`R:actions/booking.py:1181-1183`) | **Amalia** |
| Riconciliazione testo↔azione | regex COD + sostituzione del testo prima dell'invio (`A:agent.py:118-135,505-518`) | nessuna; solo `_NO_FALSE_CONFIRM_NOTE` nel prompt (`R:orchestrator.py:499-507`) | **Amalia** |
| Parsing output fallito | non esiste parsing (`A:agent.py:201-202`) | `reply_text = raw` → **blob JSON inviato al cliente** (`R:orchestrator.py:610-615`) | **Amalia** |
| Errori dei tool | `ToolResult{success,error}` al modello + istruzione di prompt (`A:tool_executor.py:96-98`, `prompts.py:139`) | read: frase generica su eccezione, ma GHL down → `ok=True` + «Nessuno slot libero» (`R:read_tools.py:180-181,231-232`); write: log e silenzio (`R:conversation_service.py:161-169`) | **Amalia** |
| Validazione argomenti | `input_schema` + ricontrollo per-handler (`A:tools.py:230-243`, `tool_executor.py:954-958`) | `payload: dict[str,Any]` libero; chiavi non documentate (`calendar_id`, `tags`, `pipeline_id`, `value`) scavalcano la config merchant | **Amalia** |
| Parametri di generazione | `temp 0.4`, `max_tokens 500` a ogni chiamata (`A:agent.py:37-38,187-193`) | né temperature né max_tokens passati; su gpt-5 temp = default provider (1.0) (`R:orchestrator.py:206-209`, `llm.py:88-91,137-139`) | **Amalia** |
| Incompatibilità famiglie modello | problema inesistente (1 modello) | `max_completion_tokens`/temp-lock gestiti con 4 test di regressione (`R:llm.py:88-99`, `tests/unit/test_llm_client.py:39-78`) | **Reloop** |
| Routing dinamico modello | nessuno, `claude-haiku-4-5` costante | 5 trigger di escalation, ma valutati una sola volta pre-loop e con curva non monotona (`R:router.py:100-112`, `orchestrator.py:147,153`) | dipende |
| Fine-tuning per tenant | assente | pipeline completa collect→filtro→presidio→FT→eval→A/B (`R:workers/fine_tuning/`) | **Reloop** |
| Prompt: identità del bot | `assistant_name` + lock anti-drift in coda (`A:prompts.py:109,226-233`) | nessuna chiave per il nome del bot in tutto il monorepo | **Amalia** |
| Prompt: grounding temporale | assente | data/ora + fuso del merchant in 2ª riga (`R:conversation_service.py:431-452,636`) | **Reloop** |
| Prompt: stato conversazionale | solo `flow_state=awaiting_address` | FSM 8 stati, sentiment, rischio escalation, continuità automazione (`R:state_machine.py:32-70`, `conversation_service.py:1446-1525`) | **Reloop** |
| Prompt: regole formato WhatsApp | «no markdown, no prefissi, messaggi brevi» (`A:prompts.py:128-139`) | nessuna regola (grep `markdown` in `ai_core/` → 0) | **Amalia** |
| Configurabilità per tenant | 1 riga `ai_configs` per store, nessuna ereditarietà | cascata profilo→merchant→agenzia→system con lock (`R:config_resolver/resolver.py:50-79`) | **Reloop** |
| Memoria conversazione | 15 messaggi fissi, nulla oltre (`A:context.py:162-167`) | 80 letti, verbatim fino a 30, poi riassunto accumulativo + ultimi 10 (`R:conversation_service.py:84`, `compressor.py:19`) | **Reloop** |
| Knowledge | FAQ inline (max 50); policy solo via tool — e `store_policy` è **parametro morto** (`A:prompts.py:101`, `agent.py:305`) | KB inline <10k token, altrimenti pgvector+HyDE+rerank; policy sempre inline (`R:retriever.py:34`, `conversation_service.py:747-779`) | dipende |
| Popolamento KB | scan automatico storefront Shopify + `/llms.txt` con anti-injection e approvazione campo-per-campo (`A:lib/ai-engine/kb-generation/`) | solo upload manuale; ma registra i buchi in `kb_gaps` (`R:retriever.py:236-257`) | dipende |
| Playground | **stessa** funzione di produzione, tool read-only eseguiti dal vivo (`A:agent.py:561-652,222-230`) | runner parallelo, **single-shot** senza tool executor, senza FSM/profili/variante (`R:playground.py:265-267`) | **Amalia** |
| Consegna umana (bolle/typing) | 1 bolla, typing fisso 1 s, payload typing non-Cloud-API (`A:agent.py:524`, `dialog360.py:103-119`) | split in 2 bolle, delay da lunghezza + jitter, typing+read receipt conformi (`R:delivery.py:101-194`, `d360_client.py:174-189`) | **Reloop** |
| Debounce inbound | `sleep(3s)` in-process + «sono l'ultimo?» (`A:agent.py:380-394`) | buffer Redis, drain atomico, finestra 8 s configurabile — ma senza cap di attesa (`R:handlers.py:134-160,242-246`) | **Reloop** |
| Finestra 24h / template | controllata solo in 1 nodo del flow builder; risposta manuale non controllata (`A:automation_step.py:265-281`, `inbox_reply.py:92-101`) | `decide_outbound` con 3 policy e 6 reason + composer protetto (`R:workers/outbound.py:84-173`, `handlers.py:1016-1034`) | **Reloop** |
| Coda outbound | outbox Postgres: SKIP LOCKED, backoff, dead-letter, reclaim (`A:outbox.py:292-467`, `reclaim.py:21-52`) | nessuna coda a stati; `enqueue_job` fallito = riga `pending` per sempre (`R:conversations.py:304-316`) | **Amalia** |
| Auto-guarigione | `safety_net.py` attivo ogni 5 min: ricostruisce dal **dominio** cosa andava inviato (`A:worker/loop.py:14-33`, `safety_net.py:719-726`) | nessun riconciliatore sul percorso conversazionale | **Amalia** |
| Identità contatto | canonicalizzatore IT/ES + match ultime 10 cifre + backfill (`A:services/phone.py:39-96`, `webhooks/whatsapp.py:719-808`) | `upsert_by_phone` con il `from` **grezzo** e uguaglianza esatta (`R:conversation_service.py:895-896`, `lead.py:38-39`) | **Amalia** |
| Tipi di messaggio gestiti | catalogo esaustivo incl. `button`, `location` con coordinate, gruppi, meta-tipi (`A:dialog360.py:222-283`) | `button` **droppato senza traccia**, `location` → segnaposto opaco (`R:webhooks.py:43-51,126`) | **Amalia** |
| Firma webhook | condizionale e **bypassabile** (omettendo l'header, o via payload Whapi) (`A:webhooks/whatsapp.py:183-202`) | HMAC obbligatorio, 401 prima del parsing (`R:routers/webhooks.py:92-102`) | **Reloop** |
| Isolamento dati | `WHERE merchant_id` scritto a mano, nessuna RLS | RLS Postgres per transazione + test isolamento a 2 tenant (`R:db/session.py:121-148`) | **Reloop** |
| Retention / DSAR | assente (solo TTL immagini try-on) | `privacy.retention_months` (default 24) + cron + erase lead (`R:schema.py:100,534`, `repositories/lead.py:163-190`) | **Reloop** |
| Handoff | UPDATE incondizionato → doppi messaggi possibili (`A:tool_executor.py:683-706`) | claim atomico SQL, dedup per turno, SLA sweep, resolve simmetrico (`R:conversation.py:429-527`) | **Reloop** |
| Takeover dall'inbox web | **non** silenzia il bot (`ai_disabled` non toccato) (`A:inbox_reply.py:105-119`) | `claim_manual_handoff` + pre-brucia l'ancora SLA (`R:conversations.py:286-296`) | **Reloop** |
| Opt-out cliente (STOP) | inesistente | esiste, ma **non applicato** ai percorsi proattivi (no_answer, lavagnetta) | **Reloop** (parziale) |
| Gate abbonamento | 2 livelli (turno AI + drain outbox) con grace 3 giorni (`A:subscription_guard.py:31-58`) | **assente**: nessun gate billing in tutto il backend | **Amalia** |
| Telemetria per turno | nessun token/latenza/modello persistito; log a stringa formattata | `model/tokens_in/tokens_out/latency_ms` su riga messaggio + evento; attribuzione variant/profile/automation/node (`R:models/conversation.py:114-145`) | **Reloop** |
| Error tracking | nessun Sentry/PostHog nel monorepo | Sentry attivo su API e worker; PostHog inizializzato ma **zero `.capture()`** | **Reloop** |
| Test motore agente | 225 test Python (15 sul prompt, 4 sul dry-run); golden fixture cross-linguaggio | 674 unit + 41 integration + 6 spec Playwright contro produzione; frontend 1 solo file di test | **Reloop** |

---

## Le differenze che contano

### CRITICHE

#### 1. Le azioni con effetti reali girano dopo l'invio della risposta — il modello non può mai raccontare l'esito

**Amalia**: ogni blocco `tool_use` viene eseguito nel momento in cui è emesso, dentro il loop (`A:agent.py:215-254`). `confirm_cod_order` aggiorna l'ordine, cancella il job di timeout, schedula il sync Shopify e fa `session.commit()` (`A:tool_executor.py:428-459`); `handoff_to_human` flippa la conversazione, invia e committa (`A:tool_executor.py:683-711`). Il `tool_result` rientra nel contesto e il modello compone il messaggio finale sapendo cosa è successo — incluso il fallimento.

**Reloop**: `await self._dispatcher.dispatch(actions, turn_ctx)` sta a `R:conversation_service.py:1943`, cioè **dopo** il loop di invio delle bolle (`:1860-1868`). Il prompt istruisce esplicitamente il modello a non dire che è fatto e a scrivere «una frase di passaggio» (`R:orchestrator.py:499-507`), poi l'handler manda un **secondo** messaggio con testo hardcoded: `f"Perfetto, ho prenotato per te l'appuntamento del {…}. Ti invieremo il promemoria."` (`R:actions/booking.py:1181-1183`, inviato da `:907-918`).

**Perché conta**: è la differenza fra agente e classificatore. Il cliente Reloop riceve sempre due bolle, la seconda fuori tono e non modulabile da persona/profilo/A-B. E se l'handler fallisce, l'eccezione viene inghiottita con un warning (`R:conversation_service.py:161-169`): il modello non lo sa, il cliente non lo sa, resta solo un log. Peggio, alcuni handler tacciono per design: `ProposeSlotsHandler` fa `return` senza messaggio se manca GHL (`R:booking.py:954`), se manca il calendario (`:960`) **e anche se semplicemente non ci sono slot** (`if suggestions:` a `:992`) — quest'ultimo è lo scenario più probabile dei tre. Reply «ti mostro subito le disponibilità» → nessun secondo messaggio → conversazione morta.

*Nota di simmetria*: il commit a metà loop di Amalia ha il difetto opposto — se il turno fallisce **dopo** il tool (es. il send a `A:agent.py:524`), l'ordine resta confermato, il cliente riceve solo la cortesia e l'AI viene spenta sul thread (`A:agent.py:744-749`).

#### 2. Nessuna riconciliazione testo↔azione in Reloop

**Amalia**: `_COD_DECISION_RE` (`A:agent.py:118-135`) più lo step di riconciliazione a `:505-518`: se esiste un ordine COD pendente e il testo dichiara conferma/annullamento ma il tool non è fra gli `executed_tools`, la risposta viene **sostituita** con una richiesta di conferma esplicita. La stessa guardia gira nel dry-run (`A:agent.py:627-634`).

**Reloop**: in `ai_core/` non esiste alcun confronto fra testo generato e azioni emesse — gli unici `re.compile` sono in `delivery.py:22-25` e `ft/anonymizer.py`. Il `CoherenceGuard` esiste ma confronta la reply con la **storia**, non con `response.actions` (`R:quality/coherence.py:45-51`), e il retry è cieco: rilancia `_run_orchestrator` con lo **stesso** `ctx` e `rc.text`, senza comunicare al modello l'`issue` rilevato (`R:conversation_service.py:1629-1634`), senza ri-verificare l'esito, alla temperatura di default del provider.

**Perché conta**: in un bot di booking «ti ho prenotato giovedì alle 15» detto senza aver emesso `book_slot` è il danno massimo, e nulla lo intercetta. Peggio: se `book_slot` viene emesso ma fallisce, l'handler può inviare subito dopo «Quello slot non è più disponibile» (`R:booking.py:1187-1189`) — due messaggi contraddittori in fila.

#### 3. Un `kind` inventato manda il JSON grezzo al cliente su WhatsApp

**Amalia**: il testo è la concatenazione dei blocchi `text` della risposta SDK (`A:agent.py:201-202`); un tool sconosciuto diventa `ToolResult(success=False, error="Unknown tool: …")` (`A:tool_executor.py:92-94`) e non tocca mai il messaggio. Questa classe di errore non esiste.

**Reloop**: `_StructuredResponse.model_validate_json(raw)` valida `kind` contro il `Literal ActionKind` (`R:orchestrator.py:27-42,360-362`). Un solo kind fuori enum, un `reply_text` mancante o un troncamento fanno fallire l'intera validazione e il ramo except imposta `reply_text = raw` (`R:orchestrator.py:610-615`). Quella stringa viene persistita (`R:conversation_service.py:1763`), passata a `split_into_bubbles` — che è puramente sintattico e non riconosce JSON (`R:delivery.py:168`) — e inviata (`:1862-1868`).

**Perché conta**: il cliente riceve `{"reply_text": "...", "actions": [...]}` come messaggio WhatsApp, e la stringa resta in cronologia come turno assistant avvelenando i turni successivi. Aggravante non ovvia: il turno gira alla **temperatura di default del provider** (1.0), perché `_complete` non passa `temperature` e su tutta la famiglia gpt-5 il campo viene comunque scartato (`R:llm.py:88-91,137-138`) mentre il modello di default è `gpt-5-mini` (`R:settings.py:91`). Massima varianza su un output che deve rispettare uno schema con enum chiusa.

#### 4. In Reloop una raffica può restare senza risposta per sempre — e l'inbox mostra che è stata inviata

**Amalia**: la finestra di accumulo non consuma nulla — i messaggi sono già righe in `messages` e il batch viene ricalcolato a ogni tentativo (`A:agent.py:428-440`). La riga outbound viene scritta **dopo** l'invio riuscito, con il wamid già in mano (`A:agent.py:524-537`).

**Reloop**: `flush_inbound_reply` fa `lrange + del(buf) + del(due)` in una MULTI (`R:workers/conversation/handlers.py:242-243`) e **poi** chiama `generate_and_send_reply` (`:254`). Il ciclo di invio (`R:conversation_service.py:1844-1868`) non è protetto da try/except, e arq **non ritenta le eccezioni normali**: solo `Retry`/`RetryJob`/`CancelledError` (`arq/worker.py:610-634`). Nel frattempo la riga assistant è già committata (`R:conversation_service.py:1760-1770`, commit a `:1822`) con `status` default `"sent"` (`R:db/models/conversation.py:111`) — `persist_assistant_message` non lo imposta mai (`R:repositories/message.py:249-266`).

**Perché conta**: un 429/5xx di 360dialog produce tre danni a catena: (1) il cliente non riceve nulla, (2) l'operatore vede in inbox una risposta marcata «inviata» che non esiste, (3) al turno successivo il bot parla come se il cliente l'avesse letta e le automazioni di follow-up considerano il thread «già risposto». Il fix è minimo — `status='pending'` all'insert e `_mark_failed` sull'eccezione, esattamente come fa già il composer (`R:routers/conversations.py:275`, `handlers.py:1202-1221`).

#### 5. Reloop non normalizza il numero inbound: lo stesso essere umano diventa due lead

**Amalia**: `A:services/phone.py:39-96` è un canonicalizzatore vero — mobile IT a 10 cifre (`:70-72`), fisso IT (`:74-76`), mobile ES a 9 cifre → +34 (`:78-83`), JID WhatsApp tagliati (`:51-54`), warning quando applica il default (`:90-95`). È un port di un contratto TS con **golden vector condivisi fra pytest e Vitest** (`A:phone.py:1-13`), perché la chiave `(store_id, whatsapp_phone)` è scritta dal Python e letta da Next.js. Sopra c'è un matcher fuzzy anti-duplicati: match esatto → digits-only su `phone` **o** `whatsapp_phone` → **ultime 10 cifre** (`A:webhooks/whatsapp.py:761-764`), preferenza per la riga collegata a Shopify (`:773`), e **backfill** della colonna canonica (`:779-788`).

**Reloop**: `R:libs/shared/src/shared/phone.py:20-25` toglie i non-digit e un `00` iniziale, e lascia passare qualunque numero senza prefisso («no per-merchant country default in V1», `:16-18`). E **sul percorso inbound non viene chiamato affatto**: `R:conversation_service.py:895-896` fa `leads.upsert_by_phone(phone=from_phone)` con il `from` grezzo, e `R:repositories/lead.py:38-39` è `WHERE Lead.phone == phone` su indice unico `(merchant_id, phone)`. Il percorso GHL invece normalizza (`R:workers/conversation/handlers.py:484,635`).

**Perché conta**: un contatto GHL salvato come `3491234567` e lo stesso umano che scrive su WhatsApp come `393491234567` diventano due lead, due conversazioni, due storici, due scoring. Non esiste alcun punto di riconciliazione né alcun test di parità.

#### 6. Il webhook di Amalia accetta payload non firmati

**Amalia**: la struttura è `if router_sig: … elif _WEBHOOK_SECRET and "channel_id" not in payload: …` (`A:webhooks/whatsapp.py:183,194`), **senza ramo `else: 401`**. Omettendo l'header `X-Relooptech-Signature` si salta il primo ramo; nel secondo, o si mette `channel_id` nel payload per essere trattati come Whapi (non verificato per design, commento a `:192-193`), oppure `_WEBHOOK_SECRET` non è configurato (`:50-54`) e il payload passa così com'è. La scelta della firma è dell'attaccante.

**Reloop**: `verify_router_signature` incondizionata, 401 prima del `request.json()` (`R:routers/webhooks.py:92-102`).

**Perché conta**: chi conosce un `phone_number_id` può iniettare conversazioni e far girare turni LLM a spese del merchant.

#### 7. Due bug reali nella codebase «di riferimento»

**`_get_product_size_info` ritorna `None` nel caso felice.** La funzione costruisce `data` a `A:tool_executor.py:873-879` e **finisce a `:880` senza return**; il `return ToolResult(...)` che dovrebbe chiuderla sta a `:1053`, dopo il return di `_create_checkout_link` (`:1048-1051`) — codice irraggiungibile. `execute_tool` la instrada con un `return await ...` (`:86-87`) e il suo try/except non intercetta nulla (restituire None non è un'eccezione). L'`AttributeError` nasce a `A:agent.py:241` (`if tool_result.success`) e risale a `handle_ai_conversation_safe` → `status="needs_human", ai_disabled=True` (`:685-703,744-749`). I due return anticipati funzionano, quindi **il bug si manifesta solo quando il tool avrebbe avuto successo**. Invisibile ai test: il tool è in `_MUTATING_TOOLS` (`:151`) e `tests/test_playground_dry_run.py:31-41` ne asserisce proprio il blocco.

**`fulfilled_order` non è filtrato per cliente né per store.** `A:services/ai_agent/context.py:189-199`: `WHERE merchant_id AND fulfillment_status='fulfilled' AND tracking_number IS NOT NULL ORDER BY fulfilledAt DESC` — nessun join sul cliente, nessun `store_id` (da confrontare con la query COD immediatamente sopra, `:175-188`, che invece fa entrambi). Il risultato entra nel prompt come «ULTIMO ORDINE SPEDITO» con numero d'ordine, data e tracking (`A:prompts.py:390-404`). Sul percorso legacy il modello può comunicare in chat il tracking dell'ordine di un altro cliente: allucinazione garantita **e** fuga di dati personali.

---

### ALTE

#### 8. Il playground di Reloop non è una preview fedele (ADR 0009 disatteso)

**Amalia**: `run_agent_dry_run` chiama lo **stesso** `_build_system_prompt_and_tools` e lo **stesso** `_run_tool_loop` della produzione con `dry_run=True` (`A:agent.py:595-623` vs `:477-490`); i tool read-only girano davvero su dati reali, solo i mutanti ricevono uno stub `{"success": true, "dryRun": true}` (`:222-230`), e si chiude con `session.rollback()` (`:650`). Anche la normalizzazione della history è duplicata apposta per non divergere (`:599-611`).

**Reloop**: `R:playground.py:265-267` chiama `orchestrator.run(ctx, msg)` **senza** `tool_executor`, quindi `max_iterations` resta il default 1 (`R:orchestrator.py:132`) mentre il live risolve 3 (`R:schema.py:372`, executor iniettato a `R:workers/runtime.py:174-186`). Mancano inoltre: hint FSM (`R:conversation_service.py:1443-1457`), dati noti del lead (`:1358-1369`), hint empatia ≥60 (`:1487-1492`), continuità automazione (`:1503-1520`), gate off-hours (`:1604-1614`), coherence guard (`:1618-1633`), compressione contesto, **`profile_id`** (`R:playground.py:225-230,243` non lo passa) e **`variant_id`** (forzato a `None`, `:256`). Gli effetti sono ri-simulati da un modulo separato che assume sempre il booking riuscito (`R:playground_sim.py:19-20`) e copre solo 4 delle 7 azioni (`:158-214`).

**Perché conta**: lo scenario più probabile è anche il peggiore. Il prompt istruisce il modello a mettere in `reply_text` **solo una frase di attesa** quando emette uno strumento (`R:orchestrator.py:393-400`); con `iterations=1` il loop esce a `is_last` (`:176`) e `final_actions` scarta i read-tool (`:185`). Il merchant che chiede «alle 15 siete liberi?» vede «un attimo che verifico» **e nient'altro** — nessun seguito, nessun evento, nessuna indicazione che manchi un pezzo. In produzione la stessa conversazione arriva agli slot reali. Chi usa i profili di conversazione (l'asse su cui Reloop ha appena investito) prova sempre il bot senza profilo.

#### 9. Trappola di configurazione: il prompt annuncia i tool anche quando il loop è spento

**Amalia**: capability e prompt sono gatati insieme nella stessa funzione (`A:agent.py:272-336`); se la lista tool è vuota il campo `tools` non viene nemmeno inviato (`:194-196`). Non esiste uno stato in cui il modello è invitato a chiamare uno strumento che il runtime non eseguirà.

**Reloop**: `_build_messages` chiama `render_schema_hint(ctx.allowed_actions)` (`R:orchestrator.py:246`) e `ctx` non porta alcuna informazione sul tool-use; la decisione avviene dopo, in `_run_orchestrator` (`R:conversation_service.py:1979-1994`). Quindi con `agent.tool_use_enabled=False`, o `max_tool_iterations=1`, o nel playground, o nel percorso proattivo (`R:orchestrator.py:328`), il prompt contiene comunque `_TOOL_USE_PARAGRAPH` che ordina «EMETTI lo strumento e metti in `reply_text` solo una frase di attesa». L'azione viene poi strippata a `:185`.

**Perché conta**: spegnere il tool-use per costo/latenza — la ragione per cui la chiave esiste — produce **deterministicamente** un bot che a ogni domanda di disponibilità risponde «controllo subito» e poi tace. La configurazione più difensiva è quella che rompe.

#### 10. Le azioni di scrittura emesse insieme a una read-tool vengono perse in silenzio

**Amalia**: ogni blocco `tool_use` di ogni iterazione viene eseguito quando è emesso e `executed_tools` accumula fra iterazioni (`A:agent.py:179,241-242`).

**Reloop**: `parsed` viene riassegnato a ogni iterazione (`R:orchestrator.py:172`) e solo l'ultimo alimenta `final_actions` (`:185-192`). Se il modello emette al giro 0 `[check_availability, book_slot]` — cosa che il prompt **incoraggia** («Puoi emettere più azioni nello stesso turno», `R:orchestrator.py:474`) — il `book_slot` evapora a meno che il modello non lo ri-emetta nel giro grounded.

**Perché conta**: prenotazioni, avanzamenti pipeline e persino `escalate_human` spariscono senza un log (vedi punto 11) e senza test (`R:tests/unit/test_orchestrator_tool_loop.py:143-165` copre solo read-poi-write). Indistinguibile da «il modello non ha voluto prenotare».

#### 11. Il tool-use di Reloop è invisibile in produzione

**Amalia**: log per iterazione con `stop_reason` (`A:agent.py:206-207`), tool richiesto con argomenti (`:220`), esecuzione lato executor (`A:tool_executor.py:62`), tool disponibili al primo giro (`:183-185`), e `recorded_tool_calls` restituiti al playground (`:652`).

**Reloop**: `_run_read_tools` logga **solo** nel ramo except (`R:orchestrator.py:232-233`); nel percorso riuscito nulla. In `read_tools.py` il `logger` è dichiarato a `:37` e **mai usato** in tutto il file. L'evento `message.replied` riporta `[a.kind for a in response.actions]` (`R:conversation_service.py:1787`), da cui i read-tool sono già stati rimossi.

**Perché conta**: dato un cliente che si lamenta («il bot mi ha detto che non c'era posto»), non esiste alcun modo di sapere se `check_availability` sia stato invocato, con quale payload, quanti slot abbia trovato o se GHL abbia risposto. Nemmeno il conteggio delle iterazioni è ricostruibile (token sommati, `:1765-1767`).

#### 12. Il fallimento di GHL viene presentato al modello come un fatto

**Amalia**: `check_inventory` ripiega su dati DB potenzialmente stale senza segnalarlo (`A:tool_executor.py:248-263`) — stesso difetto di degradazione silenziosa.

**Reloop**: `_fetch_slots` cattura `IntegrationError` e ritorna `[]` (`R:read_tools.py:180-181`); `_availability_summary` traduce la lista vuota in «Nessuno slot libero nel periodo richiesto.» (`:231-232`) con `ok=True` (`:123-128`). E `ToolResult.ok` non entra mai nell'osservazione: `R:orchestrator.py:231` usa solo `.summary` (verificato: gli unici `.ok` letti nella codebase appartengono ad `appointment_ops`, non a `orchestrator.ToolResult`).

**Perché conta**: un'API GHL giù diventa «non c'è disponibilità», detto al cliente in tono sicuro. L'opposto della degradazione onesta, e nemmeno diagnosticabile (punto 11).

#### 13. Un braccio A/B gira senza persona, senza policy, senza data corrente — e senza correzioni

**Amalia**: nessun A/B, quindi le correzioni si applicano sempre.

**Reloop**: `PromptManager.resolve_system_prompt` ritorna il body autorato della variante e **non chiama mai** il fallback (`R:prompt_manager.py:40-51`), che è l'unica strada verso `build_cascade_system_prompt` e quindi verso `build_correction_lines` (`R:conversation_service.py:584-586`). Il codice lo documenta: «playground corrections likewise apply only to the cascade fallback» (`:2176-2179`). Perdono: business info, riga data/ora, persona/tono, policy, servizi prenotabili con UUID, frammento sentiment, correzioni. Sopravvivono i blocchi appesi dopo (dati lead, FSM, empatia, continuità).

**Perché conta**: lo schema hint continua a pretendere un `service_id` «dall'elenco Servizi prenotabili del prompt» (`R:orchestrator.py:430-433`) e un ISO calcolato «rispetto alla Data e ora attuali indicata nel prompt» (`:428-429`) — riferimenti a blocchi che nell'arm variante non esistono più. L'esperimento non misura due persone, misura una persona contro un bot cieco. E il merchant smette di vedere applicate le proprie correzioni **proprio mentre misura la qualità**.

#### 14. Nessun arbitraggio fra agente AI e motore automazioni (Reloop)

**Amalia**: il trigger `message.received` del flow builder viene emesso **solo se l'AI non risponderà** e solo se lo store ha un flusso attivo — scelta di prodotto documentata nel codice: *«ONLY when the AI won't answer it (product decision: the keyword flow is a fallback for AI-off stores)»* (`A:webhooks/whatsapp.py:586-603`). Emissione in-line.

**Reloop**: `message.received` viene emesso **incondizionatamente** per ogni inbound (`R:conversation_service.py:1113-1119`), anche quando il gate ha detto no — il motivo finisce solo nelle properties (`auto_reply_skipped` + `reason`, `:1102-1114`). Il dispatcher è un **poller su `analytics_events`** con cursore Redis, lookback 120 s (`R:workers/automation/engine.py:104-113`), schedulato ogni minuto (`R:workers/settings.py:177`). Il codice ammette che «un `message_received` non ha nessun filtro naturale: un flusso sottoscritto parte su OGNI messaggio in ingresso del merchant» (`engine.py:298-303`). E `_resolve_context` calcola `ai_paused` solo da `conv.auto_reply` / `ai_disabled_until` / handoff (`engine.py:1562-1566`): **non guarda `bot.auto_reply_enabled` né `lead.opted_out_at`**.

**Perché conta**: sullo stesso inbound l'agente risponde in pochi secondi e un flusso `message_received` può rispondere fino a ~60 s dopo, da un processo che non sa nulla del turno AI. E un merchant col bot spento, o un lead che ha scritto STOP, può comunque ricevere il messaggio dell'automazione.

#### 15. L'opt-out di Reloop non copre i percorsi proattivi

**Amalia**: opt-out inesistente (grep `opt_out|unsubscribe` sul backend: zero). `Customer.is_manually_blocked` è usato solo per l'audience campagne (`A:campaigns/audience.py:665`).

**Reloop**: `lead.opted_out_at` è controllato nel gate inbound (`R:conversation_service.py:952`), al flush (`:1246`), nei trigger CRM da GHL (`R:handlers.py:551`) e nella query dormienti (`R:lead.py:261`). **Non** è controllato in `list_reminder_candidates` — la WHERE ha solo status/last_message_at/last_inbound_at/`_bot_owns_thread()` (`R:repositories/conversation.py:224-230`) — né in `_maybe_emit` (`R:workers/scheduler/no_answer.py:88-115`), né nel motore automazioni (`engine.py:1562-1566`), né nei reminder appuntamento.

**Perché conta**: un lead che scrive STOP smette di ricevere risposte, ma dopo 2 ore di silenzio scatta `lead.no_answer` e l'automazione gli manda un follow-up — cioè esattamente il messaggio non richiesto da cui l'opt-out doveva proteggerlo. Che i trigger GHL il check ce l'abbiano dimostra che è una dimenticanza, non una scelta.

#### 16. Reloop non ha alcun gate di abbonamento

**Amalia**: `is_access_allowed` con `{active, trial, trialing}` e grace di 3 giorni su `past_due` ancorata a `subscriptionPastDueAt` (`A:services/billing/subscription_guard.py:31-58`), chiamato nel turno AI dopo la finestra di accumulo (`A:agent.py:412-414`) e nel drain outbox con `status='failed'` senza retry (`A:outbox.py:326-347`). Più un ledger crediti per ogni risposta (`A:agent.py:548-553`, `credits.py:226-284`).

**Reloop**: `grep -rniE "subscription|stripe|past_due|billing"` sul backend non produce un solo gate; non esiste nemmeno un router `billing`. E non c'è aggregazione di consumo per merchant (i token esistono per messaggio ma non sono aggregati).

**Perché conta**: nessuna leva per fermare i costi OpenAI + invii WhatsApp di un tenant moroso, e nessuna misura di quanto abbia consumato.

#### 17. Nessuna rete di sicurezza sul turno perso — con un dettaglio da correggere su entrambi

**Amalia**: il turno AI gira in `BackgroundTasks` FastAPI (`A:webhooks/whatsapp.py:574-584`), quindi un redeploy a metà lo perde in silenzio; il watchdog `check_message_processed` → `retry_ai_response` è registrato (`A:jobs/dispatcher.py:112-121`) ma **nessuno lo accoda mai** (grep su tutto il repo: solo definizione e file .md di piano). **Però** — e questo il consolidato lo aveva mancato — `worker/safety_net.py` (744 righe) è **attivo ogni 5 minuti** (`A:worker/loop.py:14-33`, `_SAFETY_NET_INTERVAL=300`) e fa una cosa che Reloop non ha in nessuna forma: **ricostruisce dallo stato di dominio quali messaggi avrebbero dovuto essere programmati e li ri-accoda** — ordini COD senza `codConfirmationSentAt`, thank-you, spedizioni, i due step di cart recovery, i trigger dei flussi (`:553`) e le run bloccate (`:654`), reso sicuro dall'idempotenza degli handler (`:12-15`).

**Reloop**: nessun riconciliatore sul percorso conversazionale; gli scheduler sono edge-triggered (ADR 0015) e un invio perso resta perso. In compenso il webhook accoda su arq con `_job_id=wa:msg:{id}` (dedup ~1h) e lascia salire il 500 così il router ritenta (`R:routers/webhooks.py:13-14,135-146`), mentre Amalia risponde **sempre 200 anche in errore** (`A:webhooks/whatsapp.py:166,607-609`): un Supabase irraggiungibile durante l'INSERT fa sparire il messaggio del cliente da ogni sistema.

**Perché conta**: sono due filosofie di durabilità speculari — Amalia si ripara guardando il **dominio**, Reloop mette la durabilità **a monte** (commit dell'inbound in fase 1, transazioni corte separate, `R:conversation_service.py:1333-1334`). Nessuna delle due copre il buco dell'altra.

#### 18. Reloop non ha coda a stati, Amalia sì

**Amalia**: `job_queue` + `whatsapp_outbox` con `FOR UPDATE SKIP LOCKED` (`A:worker/job_processor.py:116`, `outbox.py:302-308`), backoff 1/5/30 min, `dead_letter` a max_retries (`job_processor.py:185-208`), reclaim allo startup dei `processing` fermi da 5 min (`A:worker/reclaim.py:21-51`), LISTEN/NOTIFY + poll 10 s.

**Reloop**: `grep -rn "with_for_update|SKIP LOCKED|pg_advisory"` sul backend → un solo hit, dentro una policy RLS di migrazione. arq non ha dead-letter e non ritenta le eccezioni. Un `enqueue_job` fallito nel composer viene solo loggato (`R:routers/conversations.py:304-316`) e la riga resta `pending` per sempre: nessuno sweeper la ridrena.

*Caveat che ridimensiona il vantaggio di Amalia*: il suo rate limiter **salta** invece di dormire (`continue` a `A:outbox.py:373-380`), e poiché il loop scorre 50 righe in millisecondi, dopo il primo invio tutti gli altri messaggi dello stesso store falliscono il `min_gap` di 2 s e restano `pending` fino al ciclo successivo (poll 10 s). Una campagna da 500 messaggi non esce a 30/min ma a circa 6/min.

#### 19. Amalia non ha throttling sul percorso AI; Reloop non ha un cap di volume

**Amalia**: rate limit (360dialog 30/min, gap 2 s) esiste **solo** nel drain outbox (`A:outbox.py:42-46,138-159,375`). L'AI reply (`A:agent.py:524`) e la risposta operatore (`A:inbox_reply.py:92`) lo bypassano. E `send_text` rilancia immediatamente su qualunque «360dialog API error» (`A:dialog360.py:56-59`): un 429 è trattato come errore permanente.

**Reloop**: `acquire_channel_slot` in cima a `_send` (`R:d360_client.py:236-238`) copre ogni invio, con retry 429/5xx che onora `Retry-After` (`:244-260`). Ma è **solo spacing** (8 msg/s = fino a 480/min, `:36`), non un cap di volume, ed è per-processo — il docstring lo dichiara: «a multi-replica deployment should graduate this to a Redis token bucket» (`R:ratelimit.py:8-11`).

#### 20. Takeover umano dall'inbox web: Amalia non silenzia il bot

**Amalia**: `/internal/inbox/send-reply` scrive `status='human_active'`, `handoff_at`, `handoff_reason='Manual reply'` — ma **non** tocca `ai_disabled`/`ai_disabled_until` (`A:inbox_reply.py:105-119`), che sono gli unici flag letti dai gate (`A:webhooks/whatsapp.py:535-555`, `agent.py:399-409`). L'operatore deve premere in più il toggle AI.

**Reloop**: `claim_manual_handoff` mette `auto_reply=false` e pre-brucia l'ancora SLA (`R:routers/conversations.py:286-296`, `repositories/conversation.py:455-485`).

**Perché conta**: in Amalia il bot parla sopra l'operatore. Solo la risposta dal *telefono* mette in pausa l'AI, e per 2 ore fisse hardcoded (`A:webhooks/whatsapp.py:496`; in Reloop è `escalation.phone_echo_pause_minutes`, `R:schema.py:530`).

#### 21. Handoff exactly-once

**Amalia**: `_handoff_to_human` fa un UPDATE incondizionato e poi invia (`A:tool_executor.py:683-706`); due turni concorrenti (album di foto) producono due «ti passo un operatore». Il fallback del safe-wrapper ha inoltre l'**ordine sbagliato**: invio (`A:agent.py:732`) → Message → `needs_human` (`:744-749`), tutto nello stesso try il cui except si limita a loggare (`:751`). Se il provider è giù, non manda il messaggio **e** non alza la bandierina.

**Reloop**: `claim_handoff` è `UPDATE … WHERE id=:id AND auto_reply = true RETURNING id` (`R:repositories/conversation.py:447-448`); il perdente non invia e non notifica (`R:conversation_service.py:1712-1718`); stesso claim per media non gestiti (`:985-987`) e nell'handler (`R:actions/escalate.py:85-93`); dedup di `escalate_human` per turno (`:1690-1694`). Il claim è **transazionale e precede l'invio**, quindi sopravvive a un fallimento di trasporto.

#### 22. Finestra 24h e template

**Amalia**: controllo solo nel nodo `action` del flow builder (`A:automation_step.py:265-281`); `send_outbound_message` non guarda mai la finestra (`A:services/whatsapp/send.py:1-22`) e la risposta manuale dall'inbox esplode con un 500 opaco (`A:inbox_reply.py:92-104`).

**Reloop**: `decide_outbound` con tre policy e sei reason motivati (`R:workers/outbound.py:84-173`), attraversato dal nodo `send`, da `ai_reply` (`R:engine.py:819-834`) e dai reminder; il composer fallisce con `error_code='outside_24h_window'` invece di tentare un invio condannato (`R:handlers.py:1024-1034`). *(Non è un punto unico: `send_message` ha un controllo inline proprio senza fallback template, `engine.py:679-690`.)*

---

### MEDIE

#### 23. Tipi di messaggio in ingresso che Reloop perde

- **`type: "button"`** (tocco su quick-reply di template): Amalia ha il ramo esplicito (`A:dialog360.py:236-238`). Reloop gestisce solo `interactive` (`R:integrations/whatsapp/webhook.py:199-204`); per `button` `text` resta `None`, non c'è placeholder (`R:routers/webhooks.py:43-51`) e il `continue` a `:126` **lo fa sparire senza persistenza né risposta**. Non è teorico: il linter template di Reloop valida i bottoni `QUICK_REPLY` (`R:integrations/whatsapp/templates.py:421-451`).
- **`location`**: Amalia porta le coordinate nel turno (`A:dialog360.py:262-266`); Reloop sostituisce «[Il cliente ha condiviso una posizione]» (`R:webhooks.py:48`) — su un prodotto che prenota appuntamenti fisici.
- **`sticker`**: è in `MEDIA_KINDS` (`R:integrations/whatsapp/media.py:16`) e ha un placeholder → genera un turno LLM completo. In Amalia è scartato (`A:webhooks/whatsapp.py:347`).
- **Meta-tipi** (`unsupported`, `ephemeral`, `system`, `edit`) e **gruppi**: Amalia li marca e li persiste per l'inbox (`A:dialog360.py:271-283`, `webhooks/whatsapp.py:350-357`); Reloop non ha nozione di nessuno dei due.
- **Gate «solo clienti noti»**: `ai_respond_only_to_known_customers` esce prima di creare cliente/conversazione/messaggio (`A:webhooks/whatsapp.py:368-384`). Nessun equivalente in Reloop: ogni inbound fa `upsert_by_phone`.

#### 24. Canale sconosciuto: 404 con retry vs drop silenzioso

Amalia risponde **404** se non trova lo store per `phone_number_id` (`A:webhooks/whatsapp.py:283-287`), lasciando che il router riprovi o metta in DLQ. Reloop accoda **prima** di sapere se il canale esiste; la risoluzione fallisce dentro il job (`R:conversation_service.py:874-877`, `reason="no_integration"`), il job termina «con successo» e il messaggio **non viene persistito da nessuna parte** — nessun retry, nessuna riga in inbox, solo una `logger.info`.

#### 25. Ordine dei blocchi nel system prompt di Reloop: i commenti mentono

Amalia ha 13 sezioni numerate con ordine dichiarato (`A:prompts.py:108-233`) e un vero blocco finale anti-drift. In Reloop l'ordine reale è `cascata (…correzioni) → dati lead → FSM → empatia → continuità → **schema hint 4540 char** → qualificazione → chunk KB → direttive playbook` (`R:orchestrator.py:244-268`). Quindi i due commenti che rivendicano la coda — correzioni «highest recency» (`R:conversation_service.py:740-741`) e continuità automazione «Injected LAST … so it wins on salience» (`:1508-1509`) — sono **entrambi falsi**: fra loro e la fine del prompt ci sono ~5000 caratteri di protocollo. Le due ottimizzazioni di salienza più recenti poggiano su un presupposto sbagliato, e nessun golden test copre il messaggio system assemblato.

#### 26. Persona e stile: quattro leve che Amalia ha e Reloop no

- **Nome dell'assistente**: `assistant_name` è il primo elemento del prompt, ripetuto nel divieto di prefissi e **blindato in coda** (`A:prompts.py:109,131,232`, default «Amalia» a `A:ai_config.py:28`). In Reloop `grep assistant_name|bot.name` su **tutto il monorepo** → zero. Il bot non ha identità nominale stabile e nulla lo trattiene dall'adottare il nome dell'operatore che appare in cronologia.
- **Rinforzo anti-drift**: «IGNORA LO STILE DEI MESSAGGI PRECEDENTI … Segui SOLO le istruzioni di tono e stile definite sopra» (`A:prompts.py:226-233`). Nessun equivalente in Reloop — che appiattisce `agent`→`assistant` (`R:conversation_service.py:96-99`) e riconosce come proattivi solo `automation`/`automation_ai` (`:112-135`), **non** i messaggi del composer umano né gli echi dal telefono. È il meccanismo dietro la regressione «dopo che l'operatore ha scritto, il bot ne eredita il registro».
- **Regole di formato WhatsApp**: «Rispondi SOLO con il messaggio», «NON aggiungere prefissi tipo 'Amalia:'», «NON usare markdown, asterischi» (`A:prompts.py:128-139`). In Reloop grep `markdown|asteris|prefiss` su `libs/ai_core/src/` → zero. Aggravante: lo splitter tratta `*` come marcatore di elenco (`R:delivery.py:24-27`), quindi un `*grassetto*` può alterare il taglio in bolle.
- **Merchant senza configurazione**: Amalia produce comunque 2631 caratteri con default espliciti (`A:agent.py:286-299`); Reloop collassa su `_default_system_prompt` — 4 frasi, **senza data/ora, senza regola di lingua, senza policy** (`R:conversation_service.py:592-619`). E `business.name` non viene mai scritto alla creazione del merchant.

#### 27. Contesto: dove ognuno spreca lavoro

- **Reloop, merchant senza KB**: `kb_tokens == 0` rende falsa `0 < kb_tokens < 10_000` e si cade nell'`elif self._embedder is not None` (`R:conversation_service.py:1377-1382`): parte HyDE (1 chiamata LLM) + embedding + query su tabella vuota, e si scrive una riga `kb_gaps` per ogni domanda (`R:retriever.py:89-104,236-257`). Ogni turno, per ogni merchant appena onboardato.
- **Reloop, fuori orario**: il check `_maybe_off_hours_message` sta a `:1605`, **dopo** l'intero assemblaggio del prompt (cascata + RAG/HyDE/rerank + playbook), e il sentiment nano (`:1740-1747`) è fuori dal ramo if/else quindi gira anche per una risposta sintetica. Nessun dedup: tre raffiche notturne = tre volte lo stesso testo identico, tre `message.replied`.
- **Amalia, percorso AI Engine**: `assemble_agent_context` calcola e butta `ctx.customer` (`A:context.py:217`), `ctx.fulfilled_order` (`:189-199`), `ctx.relevant_products` (`:266-275`) e la query `StorePolicy` (`A:agent.py:64` → passato `None` a `:305`). Quattro query per turno il cui risultato non raggiunge mai il modello.
- **Amalia, vocali**: la trascrizione Whisper viene fatta **due volte** — nel BackgroundTask del webhook, salvata in `metadata.transcription` (`A:webhooks/whatsapp.py:868-887`), e di nuovo nell'agente sui byte in RAM (`A:agent.py:462-465`). La versione persistita non viene mai riletta. Reloop la rilegge dal messaggio (`R:conversation_service.py:1313-1319`).

#### 28. La coalescenza rompe il contesto multimediale in entrambi, in modi opposti

**Reloop**: il flush passa solo `wa_ids[-1]` (`R:handlers.py:258`) e `_resolve_current_media` risolve la vision su quell'unico id (`R:conversation_service.py:1265,1293-1328`). Foto seguita da «che ne pensi?» → l'immagine non arriva al modello, resta il segnaposto, e il prompt afferma attivamente che il modello non può vedere i media (`R:orchestrator.py:476-485`) — causando esattamente la risposta che `_MEDIA_VIEWABLE_NOTE` doveva evitare.

**Amalia**: quando c'è media, il ramo a `A:agent.py:455-465` sostituisce `current_user_content` e i `pending_msgs` accumulati vengono **ignorati** (il join sta nell'`else` a `:467`). Chi scrive «quanto costa questo?» e poi manda la foto ottiene una risposta che non ha visto la domanda.

#### 29. Debounce senza cap in Reloop

Ogni inbound riscrive `due = now + window` (`R:handlers.py:138,143`) e il flush si ri-schedula finché `now < due` (`:226-238`). Nessuna chiave `max_wait`/`debounce_max` esiste (grep su `libs/`, `workers/`, `services/`). Un utente che scrive una riga ogni 6-7 s con finestra 8 s **non riceve mai** la prima risposta finché non tace. Amalia ha una finestra fissa e non estendibile di 3 s (`A:agent.py:41,380`). Sommato al delay typing (fino a 6 s), la latenza percepita out-of-the-box di Reloop è ~9-15 s contro ~4-5 s.

Aggravante di percezione: l'indicatore «sta scrivendo» di Reloop viene emesso a `R:conversation_service.py:1840`, cioè **dopo** debounce + RAG + tool-loop + coherence retry. Copre gli ultimi 1-6 s di una latenza di 15, non i primi.

#### 30. Ritmo umano sugli invii proattivi: invertito

Su Reloop il ritmo umano vale **solo** per la risposta inbound: `send_decision` chiama `sender.send_text` diretto, senza bolle né delay (`R:workers/outbound.py:176-192`). In Amalia ogni testo proattivo passa da `send_text_with_typing` senza `typing_seconds`, quindi con la scala 3/4/5 s per lunghezza (`A:outbox.py:406-412`, `parser.py:72-78`). Un flusso Reloop con tre nodi `send` consecutivi consegna tre messaggi nello stesso decimo di secondo.

#### 31. Sanitizzazione e cap lunghezza

Amalia sanifica ogni testo libero e ogni parametro template, con parità byte-a-byte dichiarata col gemello TS e vettori golden condivisi (`A:services/whatsapp/sanitize.py:1-26`, `dialog360.py:80,137`, `outbox.py:74`). Reloop invia `text` grezzo (`R:d360_client.py:72-80`) e `build_send_components` impacchetta `str(v)` senza pulizia (`R:templates.py:611-616,646-652`, stesso buco nel composer a `R:handlers.py:1050-1054`): un nome lead con newline importato da GHL fa rifiutare il template con `(#100) Invalid parameter`. Sul cap: Amalia è protetta di riflesso dai 500 token; Reloop non passa `max_tokens` e `_rebalance` fonde le bolle in eccesso **superando volutamente** `max_chars` (`R:delivery.py:155-165`) — nessun troncamento a 4096 esiste nella catena.

#### 32. Risposta vuota

Amalia: `if not response_text: … skipping send` (`A:agent.py:497-499`). Reloop: `split_into_bubbles("")` ritorna `[]` ma `… or [response.reply_text]` ricade su `[""]` (`R:conversation_service.py:1832-1838`) → `send_text` con body vuoto → 400 → turno perso (e, per il punto 4, nessun retry).

#### 33. Fine dell'episodio: Reloop la chiude, Amalia ha codice morto

Reloop: `close_idle_active` (`R:repositories/conversation.py:258-322`) con floor per-merchant derivato dal ritardo più lungo configurato sulla lavagnetta (`:288-297`), e la chiusura fa **decadere il profilo di conversazione** tornando al default (`_reset_profiles_to_default`, `:326-349`). Amalia ha `auto_close_conversation` (`A:jobs/handlers/auto_close_conversation.py:1-57`) registrato nel dispatcher (`:100-103`) ma **nessuno lo pianifica mai**: una conversazione resta `active`/`needs_human` per sempre, e con essa `ai_disabled=True` scritto dall'handoff automatico (`A:webhooks/whatsapp.py:896-905`). L'unico modo di riaccendere il bot è il toggle manuale.

#### 34. Isolamento dati e confine transazionale

Reloop: RLS Postgres con `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', …, true)` per transazione (`R:db/session.py:121-148`), transazioni corte e indipendenti (persist → contesto → generazione → update wamid → ogni handler la propria). Amalia: **nessuna RLS**, isolamento via `WHERE merchant_id` scritto a mano; una **sola** sessione condivisa fra webhook, tool loop e fallback — con la conseguenza documentata nel codice (`A:agent.py:689-696`: rollback esplicito necessario «otherwise … the customer would get neither an AI reply nor the fallback») e un SAVEPOINT difensivo a `:90`. In compenso Amalia ha **due database distinti**, business e coda (`A:core/database.py:4-29`), che rendono impossibile per costruzione un enqueue transazionale.

Il rovescio in Reloop: ogni read-tool apre la **propria** sessione tenant (`R:read_tools.py:71,193`, refresh token in una terza a `:146`) dentro un turno che ne tiene già una aperta (`R:conversation_service.py:885`), per la durata di chiamate HTTP a GHL (timeout 15 s). Con 3 iterazioni sono fino a 4 connessioni Supavisor concorrenti per turno.

#### 35. Il client OpenAI viene ricostruito a ogni turno

`ModelRouter.select()` costruisce un `OpenAIClient` nuovo su ogni ramo (`R:router.py:65,68,77,87,89`) e `_get_client()` crea l'`AsyncOpenAI` sull'istanza (`R:llm.py:112-117`), che vive quanto il turno; nessuno chiama `.close()`. Amalia ha un singleton di modulo (`A:agent.py:34`). Ogni turno WhatsApp paga un handshake TLS completo verso api.openai.com. Il contrasto interno è illuminante: il client nano della RAG è invece memoizzato sul servizio (`R:conversation_service.py:1957-1969`).

#### 36. Il fallback Anthropic di Reloop è rotto in tre modi (ma è spento)

`anthropic_fallback_enabled: bool = False` di default (`R:settings.py:86`), quindi `ModelRouter.fallback()` ritorna `None` (`R:router.py:93`) e l'eccezione va al fail-safe. Se venisse acceso: (1) `AnthropicClient.complete` dichiara `response_format` a `R:llm.py:187` e **non lo usa mai** nel corpo (`:191-210`) — la fix del 23/06 è cosmetica; (2) `_to_chat_history` non fonde i ruoli consecutivi né garantisce che il primo turno sia `user` (`R:conversation_service.py:87-99`), mentre la Messages API lo richiede — e le conversazioni aperte da automazione hanno un primo turno assistant; (3) il cap output diventa 1024 (`R:llm.py:208`) contro nessun cap sul primario.

#### 37. Timeout, retry e budget

Nessuna delle due imposta `max_retries` (default Stainless = 2). Reloop: timeout 30 s (`R:llm.py:105`) → ~95-100 s reali per iterazione; l'Embedder è **senza** timeout (`R:retriever.py:273`) e Whisper a 60 s (`R:media_pipeline.py:135`). Amalia: nessun timeout esplicito → default SDK 600 s × 3 = fino a ~30 minuti su una `BackgroundTask` che regge la sessione DB. Il cap 500 token di Amalia inoltre non è gratis: l'uscita del loop è `stop_reason == "end_turn" or not has_tool_use` (`A:agent.py:210`), quindi con `stop_reason == "max_tokens"` **e** `has_tool_use=True` il loop prova a eseguire un tool con `input` tagliato a metà.

#### 38. Ordinamento dello storico

Amalia ordina per `sentAt DESC` — timestamp Meta a risoluzione di **secondo**, senza tiebreaker (`A:context.py:161-166`, parsing a `A:dialog360.py:456-460`). Reloop per `Message.created_at DESC` — istante di ingestione, microsecondi (`R:repositories/message.py:49,76`). Conseguenze opposte: in Amalia due messaggi nello stesso secondo entrano in ordine non deterministico (ed è la stessa granularità su cui poggia il de-duplicatore del batch a `A:agent.py:382-390`, con un rischio residuo di doppia/zero risposta), ma un webhook ritardato si ricolloca correttamente nel tempo reale; in Reloop l'ordine è stabile ma un webhook ritardato **si accoda in fondo** e la cronologia mostrata al modello diverge da quella sul telefono del cliente.

#### 39. Capability gating: buchi opposti

**Amalia**: nel ramo legacy `tools = list(TOOL_DEFINITIONS)` senza `get_filtered_tools` (`A:agent.py:326-328`), quindi anche `create_cart`/`create_checkout_link` gatati da `can_create_orders` (default False, `A:ai_config.py:76`). In produzione il ramo è di fatto irraggiungibile — `ai_engine_enabled=False` blocca a monte (`A:webhooks/whatsapp.py:535`, `automations/dispatcher.py:51-52`) — **ma il playground gira su quel ramo**, quindi mostra un agente con più poteri di quello reale. In più `can_search_products` e `can_answer_questions` sono accettati dal builder e non usati nel testo (`A:prompts.py:91-93`): con `can_answer_questions=False` il prompt istruisce comunque a usare `get_store_policies` (`:177`), tool che `get_filtered_tools` ha appena rimosso (`A:tools.py:381-382`). E `can_recover_carts`/`can_suggest_virtual_tryon` non gatano nulla (zero occorrenze fuori dal modello).

**Reloop**: `booking.enabled` cambia **solo** il testo del prompt — grep esaustivo: compare a `R:playbook.py:35,102` e `conversation_service.py:552,618,646,732`, tutti punti di costruzione del prompt, **mai** per costruire `allowed_actions`. Un merchant che ha disattivato le prenotazioni può farsi emettere `book_slot`: l'handler è registrato, il calendario configurato, l'appuntamento viene creato. Simmetricamente `lead_capture.enabled` (`:553,689`). *(Non tutti i flag sono cosmetici: `pipeline_auto_advance` e `scoring_enabled` gatano comportamento reale, `:1906,1932`.)*

#### 40. `escalate_human` non è la valvola di sicurezza che il codice promette

Il docstring di `render_schema_hint` dice «`escalate_human` is always kept as a safety valve unless the allowlist is explicitly empty» (`R:orchestrator.py:530-532`), ma il codice fa solo `allow = set(allowed) | {"none"}` (`:541`). Se il merchant configura `conversation.playbook.actions.enabled` senza `escalate_human`, l'azione sparisce dallo schema **e** viene filtrata dalle azioni accettate (`:188-189`). All'inverso, con `escalation.enabled=False` il prompt continua ad annunciarla e lo scarto avviene solo a valle (`R:conversation_service.py:1695-1705`), sprecando la generazione. In Amalia `handoff_to_human` non compare in nessun ramo di esclusione (`A:tools.py:365-384`): non esiste combinazione di flag che lo tolga.

#### 41. Le azioni iniettate dal server scavalcano l'allowlist

`_with_score_action` (`R:conversation_service.py:1922 → 2302-2325`) e `_with_pipeline_advance_action` (`:1932 → 2328-2363`) aggiungono `update_score` e `move_pipeline` **dopo** l'orchestratore, e la lista arricchita va dritta a `dispatch` (`:1943`). I gate sono `caps.scoring_enabled`/`caps.pipeline_auto_advance`, chiavi diverse da `conversation.playbook.actions.enabled`. Un playbook che dichiara `actions.enabled: ["escalate_human"]` — un bot esplicitamente non commerciale — continua a scrivere il lead score e a spostare il contatto di stage nel CRM.

#### 42. `move_pipeline`: lo schema documenta `"stage"`, l'handler legge `"stage_id"`

Lo snippet mostrato al modello dichiara `payload: { "stage": "<nome stage target, opzionale>" }` (`R:orchestrator.py:447-452`). `MovePipelineHandler` legge `action.payload.get("stage_id")` (`R:actions/pipeline.py:89`) e, non trovandolo, ricade sempre su `PIPELINE_QUALIFIED_STAGE_ID`. La chiave `stage` non è letta da nessuna parte. Paradossalmente l'azione **iniettata dal server** funziona (usa `stage_id`, `:2356`), quella del modello no. Nessun test lo copre.

#### 43. Idempotenza delle scritture

Amalia: `confirm_cod_order` filtra `cod_status == "pending"` (`A:tool_executor.py:418`), una seconda chiamata torna «Order not found or not in pending status». Reloop: `AppointmentRepository.record_booking` fa `session.add(Appointment(...))` + flush senza upsert (`R:repositories/appointment.py:137-138`); il vincolo unico `uq_appointments_merchant_ghl_event` esiste ma è usato solo da `upsert_by_ghl_id` (`:189-190`), la via del reconcile poll. Due `book_slot` nello stesso JSON → due righe e due conferme (il dispatcher li esegue in sequenza, `R:conversation_service.py:156-162`).

#### 44. Override di modello per nodo automazione senza rete

Il nodo `ai_reply` passa `force_model=cfg["model_override"]` e `ModelRouter.select` costruisce direttamente `OpenAIClient(model=...)` saltando ogni controllo (`R:engine.py:795`, `router.py:64-65`); la UI espone il campo come testo libero (`automation-nodes.tsx:247`). `_evaluate_ai_check` ha un `except` (`engine.py:1123`), `_do_ai_reply` **no**: l'eccezione risale attraverso `_walk` e `automation_run` (solo `try/finally`, `:424-437`). E `WorkerSettings` non imposta `max_tries` (`R:workers/settings.py:133`), quindi default arq = 5: un typo del merchant fa ri-camminare il grafo cinque volte, con protezione anti-doppio-invio solo per le continuazioni dei `wait` (`:438-448`).

#### 45. Il percorso proattivo di Reloop è un secondo motore con garanzie inferiori

`_build_proactive_messages` (`R:orchestrator.py:325-357`) è un **quarto** assemblatore di prompt: niente commutazione nota media, niente dati lead, niente FSM, niente rischio escalation, versione accorciata del blocco qualificazione, history 30 invece di 80 (`R:engine.py:913`), obiettivo come ultimo messaggio `user` invece che blocco system. Il nodo passa `kb_chunks=[]` (`R:engine.py:783`) — l'AI proattiva non vede la knowledge base — e `run_proactive` scarta comunque le read-tool (`R:orchestrator.py:312`), quindi non ha grounding sul calendario. Non riceve `prior_sentiment` né `customer_message` (`R:engine.py:914-916`), quindi **niente correzioni playground**. È il percorso che genera il **primo contatto** con il lead, ed è quello con meno contesto di tutti.

*(In Amalia il gemello strutturale esiste ed è ancora più povero: `handle_cod_reply` intercetta l'inbound prima dell'agente, classifica con una chiamata single-shot su `text[:500]` e un system prompt fisso — zero storico, zero persona — e invia un ack **hardcoded** (`A:automations/cod_reply.py:99-132,212-222`, dispatcher `:41-50`).)*

---

## Differenze minori

- **Loop annidati / ricorsione**: nessuno dei due ne ha; entrambi un `for` piatto (`A:agent.py:182` / `R:orchestrator.py:166`).
- **Terminazione del loop**: Amalia usa `stop_reason == "end_turn" or not has_tool_use` (`A:agent.py:210`) — il primo operando è di fatto ridondante, quindi entrambi decidono con la stessa euristica «nessun tool = ho finito». Reloop però non ha proprio `stop_reason` in `CompletionResult` (`R:llm.py:34-41`).
- **Cap iterazioni**: 5 hardcoded (`A:agent.py:39`) vs 3 configurabile per merchant con range 1-5 (`R:schema.py:743`). Aritmetica: 3 iterazioni = 2 round di tool visti; 5 = fino a 4 visti (il 5° eseguito ma mai riletto).
- **Selezione modello**: Reloop sceglie il client **una volta** prima del loop (`R:orchestrator.py:159`), quindi il contesto che cresce dentro il loop non può far scattare l'escalation. E la curva è non monotona: 0-14 messaggi → mini, 15-30 → `gpt-5.2` (`many_turns`), 31+ → di nuovo mini perché la compressione porta `turn_count` a 11 (`R:conversation_service.py:1573-1577`, `router.py:110`).
- **Enum `purpose` del router**: `classification` ed `escalation` non hanno alcun call-site (unico uso: `R:sentiment.py:44`); il classificatore obiezioni bypassa il router costruendo l'`OpenAIClient` a mano (`R:workers/scheduler/objections.py:76-79`) con `max_tokens=600` mappato a `max_completion_tokens` su un modello di ragionamento — se il budget si esaurisce prima del JSON, `objections.py:83-85` ritorna `[]` in silenzio.
- **Sentiment per turno**: `SentimentAnalyzer.analyze` gira su ogni inbound su `gpt-5-nano` **senza max_tokens e senza temperature** (`R:sentiment.py:48-53`) per una classificazione di una parola, senza pre-filtro lessicale. Amalia ha un regex-first davanti al suo classificatore e `max_tokens=10` (`A:cod_reply.py:113-140`).
- **Id modello hardcoded in Reloop**: `gpt-4.1-nano` a `R:conversation_service.py:1967` e `playground.py:124`, `gpt-4.1-mini` a `R:routers/analytics.py:610`, nonostante il completion-plan dichiari «niente più ID hardcoded». **Ma** è proprio quell'hardcoding a rendere sicuri i cap stretti dei sotto-agenti (rerank 80, coherence 80, compressione 300, HyDE 150): instradarli su `gpt-5-nano` spegnerebbe quattro feature in silenzio, tutte fail-open.
- **Temperature effettiva incoerente in Reloop**: default provider sui gpt-5, 0.3 sui fine-tune `ft:gpt-4.1-mini`. Il gate di qualità FT confronta baseline (`gpt-5-mini`, temp omessa) e FT (temp 0.3) — apples-to-oranges anche sul parametro (`R:workers/fine_tuning/evaluate.py:149-155,221-223`).
- **Lingua**: Amalia mirroring del cliente sul ramo AI Engine (`A:prompts.py:137`), italiano-first con deroga sul legacy (`:434`). Reloop «REGOLA ASSOLUTA DI LINGUA … qualunque sia la lingua usata dal cliente» (`R:conversation_service.py:696-701`) — coerenza di brand, ma risponde in italiano a un cliente inglese se il merchant non cambia `bot.language`. E la regola esiste solo nel ramo `has_profile`.
- **Blocco KB anonimo e in inglese**: `f"Knowledge base context:\n{kb_snippet}"` con numerazione `[1] [2]` (`R:orchestrator.py:259-263`) dentro un prompt altrimenti italiano, senza una frase che dica al modello cosa farne o che precedenza abbia. Su KB piccole è potenzialmente il pezzo più grosso del prompt. Amalia usa «DOMANDE FREQUENTI:» con `D:`/`R:` (`A:prompts.py:191-193`).
- **Descrizioni tool in inglese per scelta dichiarata** in Amalia (`A:tools.py:5-6`), separando lessicalmente il livello di controllo da quello conversazionale; in Reloop schema e note sono tutti in italiano.
- **Costo fisso di protocollo**: schema hint Reloop = **4540 caratteri** misurati (4363 in variante vision); `TOOL_DEFINITIONS` Amalia = **9133 caratteri di JSON** inviati a ogni iterazione. Amalia paga il doppio come payload fisso — il suo vantaggio è di forma (il contratto non è testo di sistema), non di costo.
- **Nessun prompt caching** in nessuna delle due (`cache_control` → zero occorrenze). Nessun `reasoning_effort`, `top_p`, `top_k`, `seed`, `stop_sequences`.
- **Read receipt**: solo Reloop li manda, ma sono accorpati al typing indicator — spegnere il typing spegne anche le spunte blu (`R:conversation_service.py:1840,1858`).
- **Callback di consegna**: Reloop ha un rank monotono `pending<sent<delivered<read` che ignora i callback all'indietro e non sovrascrive `failed` (`R:handlers.py:1148-1155`), ma con multi-bolla solo `_last_wamid` è scritto sulla riga (`R:conversation_service.py:1870-1880`) — metà dei callback finisce in `row_missing`. L'ADR 0008 documenta il limite al contrario («la **prima** bolla»).
- **`send_interactive` è implementata e mai cablata** in Reloop (`R:d360_client.py:191-216`, assente dal Protocol `WhatsAppSender`, `factory.py:22-38`). Amalia parsa i `button_reply`/`list_reply` in ingresso ma non ha alcun metodo di invio interattivo.
- **Nessuno dei due invia media free-form in uscita**; l'unico media outbound è l'header IMAGE dentro un template.
- **Tokenizzazione delle correzioni divergente**: Amalia `str.split()` (punteggiatura attaccata, `A:prompts.py:44,56`), Reloop `re.findall(r"\w+")` (`R:corrections.py:31`). Sulle domande dei clienti, che finiscono quasi sempre con «?», lo score cambia sistematicamente. Bacino diverso anche: 20 più recenti per store senza flag (Amalia) vs tutte le attive per merchant (Reloop).
- **UI delle correzioni**: il `PATCH .../corrections/{id}` che porta `is_active` **non è chiamato da nessun file del frontend** Reloop (`corrections-panel.tsx:29-41` fa solo GET+DELETE); in pratica si silenzia cancellando, come in Amalia. Amalia in compenso ha lista paginata + creazione manuale fuori dal playground (`A:ConfigurationPage.tsx:208-243`).
- **Whapi**: Amalia ha un **secondo trasporto** non ufficiale, commutabile per-store (`A:services/whatsapp/whapi.py`, `store_provider.py:45-60`), senza template e quindi senza vincolo 24h. Reloop ha un solo trasporto (`R:factory.py:39-60`).
- **Log del contenuto**: Amalia scrive il testo delle risposte nei log (primi 100/200 char, `A:agent.py:206-207,557-558`) e gli argomenti completi dei tool (`:220`) — utile per il debug, discutibile lato GDPR. Reloop non logga mai contenuto.
- **Versioni SDK**: Reloop `anthropic 0.96.0` / `openai 2.32.0`; Amalia `anthropic 0.88.0` / `openai 2.30.0`.
- **Whisper duplicato in Amalia**: due implementazioni con semantica di errore opposta (`A:transcription.py:12-27` ritorna None; `A:agent.py:755-771` ritorna un testo di comodo — di fatto morta).
- **Toggle morti**: `can_recover_carts`/`can_suggest_virtual_tryon` (Amalia), `handoff_threshold`, `brand_voice_notes` (mai passato), `store_policy` (parametro morto). In Reloop `bot.first_message` viene iniettato a **ogni** turno pur riguardando solo il primo messaggio (`R:conversation_service.py:711-715`).
- **`optimal_send_hour`**: il cron settimanale lo calcola e lo scrive (`R:workers/scheduler/send_time.py:56-68`), viene caricato in `ReminderCandidate` (`R:repositories/conversation.py:52,219,251`) e **mai letto da nessuno**. Il docstring a `:7` afferma il contrario. Loop aperto.
- **`kb_gaps` non si accende per i merchant con KB piccola** (il ramo inline salta `retrieve()`) e non ha alcuna UI: gli endpoint compaiono solo nel client OpenAPI generato.
- **Endpoint orfani in Reloop**: `/analytics/merchant/objection-trends` e `/analytics/merchant/lead-scores` non sono chiamati da nessun componente, e dichiarano `require_role({"merchant_admin","tenant_admin",...})` mentre `KNOWN_ROLES = {agency_admin, merchant_user}` (`R:shared/constants.py:12-17`) — due dei tre ruoli non esistono.
- **PostHog**: `init_posthog` è chiamato ma **zero `.capture()`** in tutto il backend Reloop. La CLAUDE.md è da correggere: Sentry sì, PostHog è debito, non capability.
- **Bug cross-tenant ancora aperto**: `apply_status_by_name(merchant_id=None)` sul path webhook (`R:workers/scheduler/template_sync.py:36-45`).
- **Playground di Amalia — correzione all'audit consolidato**: restituisce `tool_calls` e `handoff` (`A:internal/ai_playground.py:46-49,74-78`); manca token/latenza/modello, non la diagnostica del tool-use — ed è l'unico dei due a mostrare i tool realmente eseguiti.

---

## Dove Reloop è superiore

1. **Configurazione multi-tenant**: cascata profilo→merchant→agenzia→system risolta per-foglia con cache Redis e lock lato admin (`R:config_resolver/resolver.py:50-79`, `playbook.py:76-95`). Amalia ha una riga `ai_configs` per store, zero ereditarietà, zero nozione di agenzia. Un'agenzia Reloop può standardizzare tono e regole su tutti i merchant e cambiare persona **per conversazione** caricando un profilo da automazione (ADR 0022).
2. **Grounding temporale**: data/ora nel fuso del merchant come seconda riga, con istruzione esplicita per «oggi/domani» (`R:conversation_service.py:431-452`) e riferimento incrociato dallo schema di `book_slot` (`R:orchestrator.py:428-429`). Amalia non sa che giorno è, e nel frattempo il suo prompt le chiede di decidere se siamo fuori orario (`A:prompts.py:220-223`).
3. **Orari operativi deterministici**: `_maybe_off_hours_message` corto-circuita l'LLM (`R:conversation_service.py:1601-1615`), più orari strutturati per giorno con pause e chiusure eccezionali nel prompt (`:657-675`).
4. **Memoria a lungo termine**: 30 turni verbatim, poi riassunto accumulativo persistito su `conversations.context_summary` + ultimi 10 (`R:quality/compressor.py:46-95`). Amalia dimentica tutto oltre 15 messaggi — e i 15 sono condivisi con il batch di inbound pendenti, quindi con 5 messaggi di fila la history utile scende a 10.
5. **Modello di tono a assi ortogonali**: `formality` × `verbosity` × `emoji_policy` con enum validati e frammenti deterministici (`R:conversation_service.py:382-404`), più `bot.examples` few-shot fino a 5 coppie (`R:schema.py:471-491`). Amalia ha 5 preset chiusi che mescolano registro, emoji e verbosità in una frase (`A:prompts.py:26-35`): cambiare l'emoji policy obbliga a cambiare il registro.
6. **Continuità con i messaggi di automazione**: blocco che ri-inietta il testo proattivo e vieta di ripresentarsi (`R:conversation_service.py:1502-1525`), con soppressione dell'hint FSM GREETING (`:1450-1454`). È il fix di un bug reale che Amalia non ha nemmeno gli strumenti per diagnosticare (carica `sender_type` in `RecentMessage` e non lo usa mai, `A:context.py:92,235` vs `agent.py:446`).
7. **Adattamento affettivo**: frammento sentiment del turno precedente + avviso di frustrazione a rischio ≥60 con evento oltre 75 (`R:conversation_service.py:405-417,1458-1500`). Amalia arriva all'handoff senza gradazioni.
8. **Istruzioni sui media commutabili**: `_MEDIA_NOTE` vs `_MEDIA_VIEWABLE_NOTE` a seconda che ci sia davvero un'immagine allegata (`R:orchestrator.py:246,476-497`), e il media sopravvive al retry perché è riletto dallo storage (`R:conversation_service.py:1293-1328`). Il prompt AI Engine di Amalia non menziona mai i media, e un retry perde i byte (`A:jobs/handlers/retry_ai_response.py:98-99`).
9. **Consegna umana**: split in bolle che non taglia mai a metà parola e tiene unite le liste (`R:delivery.py:129-194`), delay da lunghezza con jitter deterministico, typing+read receipt conformi Cloud API. Amalia manda un muro di testo dopo 1 secondo fisso, con un payload typing che su 360dialog quasi certamente non produce nulla (e l'errore è swallowed).
10. **Finestra 24h come esito applicativo leggibile** invece che errore opaco del provider.
11. **Handoff exactly-once + SLA come episodio**: claim atomico, dedup per turno, sweep edge-triggered ogni 5 min con soglia per merchant che non brucia l'ancora se nessuno ascolta (`R:workers/scheduler/handoff_sla.py:80-105`), `resolve_handoff` che ripristina lo stato FSM pre-handoff (`R:repositories/conversation.py:503-528`).
12. **Invariante «ogni send proattivo lascia una riga in inbox»** con attribuzione tipizzata: 8 `sender_type` + `automation_id`/`automation_node_key`/`profile_id`/`variant_id`/`reply_to_message_id` (`R:models/conversation.py:100-145`, `repositories/message.py:177-234`). Amalia marca tutto «ai». *(Crepa: `_send_proactive` ripiega su `send_decision` senza persistenza quando `conversation_id is None`, `R:engine.py:596-597`.)*
13. **Azioni iniettate dal server**: scoring cumulativo e avanzamento pipeline deterministici e non allucinabili (`R:conversation_service.py:2302-2363`), con whitelist dei signal (`R:actions/scoring.py:38-49`).
14. **Ricchezza degli effetti CRM**: `move_pipeline` fa upsert contatto con custom field mappati, tag, ricerca/creazione opportunity, spostamento stage, nota interna con sentiment, persistenza su lead, log `ghl_sync` ed evento (`R:actions/pipeline.py:89-224`). L'effetto più ricco di Amalia è aggiornare uno stato COD e schedulare un job.
15. **Degradazione dichiarata quando GHL non è collegato**: modalità agenda locale con istruzione operativa al modello e appuntamento comunque salvato con promemoria (`R:read_tools.py:76-87`, `booking.py:283-350`).
16. **RAG serio**: pgvector + HyDE + rerank + freshness decay + `min_score` + gap detection (`R:rag/retriever.py:66-258`), contro zero retrieval semantico in Amalia (`generate_embedding` è uno stub dichiarato tale).
17. **Fine-tuning per tenant end-to-end**: collect con filtro esiti positivi, filtro qualità che scarta i fallback del bot, anonimizzazione regex+presidio, split held-out 15%, eval contro baseline con gate, deploy come arm A/B via `FtModelResolver` iniettato in entrambi i router (`R:workers/fine_tuning/`, `ft_routing.py:30-78`, `runtime.py:105`, `api/main.py:79`).
18. **A/B con statistica vera** (z-test a due proporzioni) + bandit Thompson cablato nell'assegnazione (`R:ab_stats.py:43-71`, `bandit/thompson.py:20-55`, `conversation_service.py:2437-2477`).
19. **Analisi post-conversazione**: estrazione obiezioni auto-innescata dal cron di chiusura, trend week-over-week con rebuttal suggerito, sentiment per turno, predizione rischio escalation.
20. **Esiti configurabili dal merchant**: `OutcomeDefinition` + `LeadOutcome` con `source` e `cardinality` resa idempotente da indici unique parziali (`R:db/models/outcome.py:8-46`), nodo `emit_outcome` con tendina invece di stringa libera.
21. **Telemetria per turno**: modello risolto dal provider, token, latenza persistiti (`R:conversation_service.py:1762-1789`) — inclusi i turni non-LLM etichettati `model="off_hours"`/`"error_fallback"`.
22. **Catalogo eventi tipato con meta-test AST** che pretende che ogni `event_type=` letterale sia registrato (`R:db/analytics_events.py`, `tests/unit/test_analytics_events.py:33-60`), nato dal bug `reminder.sent` vs `appointment_reminder.sent` che rendeva una metrica sempre 0.
23. **Sentry** su API e worker con `release=git_sha` e tag servizio.
24. **Sicurezza dati**: RLS per transazione, retention configurabile con cron, DSAR che sostituisce il telefono con `erased:{id}` invece di violare i vincoli (`R:repositories/lead.py:163-190`).
25. **Firma webhook obbligatoria** e contratto 2xx/4xx/5xx col router esplicito.
26. **Copertura test backend**: 674 unit + 41 integration (isolamento RLS a 2 tenant) + 6 spec Playwright **contro produzione**, incluso `05-kb-rag` che verifica upload→indicizzazione→chunk→risposta fondata. Amalia non ha un solo test su `tool_executor.py` (1053 righe) — ed è il motivo per cui il return morto di `_get_product_size_info` è in produzione.
27. **Gestione delle incompatibilità fra famiglie di modelli** con test di regressione nati dai findings E2E.
28. **Playground con diagnostica**: modello, token, latenza, chunk KB con score, stato lead simulato, eventi, bolle con delay; più `POST /playground/apply` che promuove le regole ad-hoc a override persistente con blocco delimitato e invalidazione cache (`R:routers/playground.py:122-203`).

---

## Sostanzialmente identici

- **Esecuzione dei tool/azioni rigorosamente seriale**, mai in parallelo; nessuna ricorsione, nessun loop annidato; nessun `asyncio.gather`.
- **Nessuno dei due invia media in uscita free-form**, nessun carousel, nessun bottone/lista interattiva realmente cablato.
- **Entrambi partono con il bot MUTO**: in Reloop `bot.auto_reply_enabled=False` (`R:schema.py:497`), in Amalia il gate reale è `ai_engine_enabled` con default False (`A:store.py:241`, letto a `A:webhooks/whatsapp.py:535` e `dispatcher.py:51-52`) — `store.ai_enabled=True` è solo un secondo controllo interno all'agente. Nessuna differenza filosofica.
- **Interruttore AI a più livelli** concettualmente equivalente (merchant/store + conversazione + pausa a tempo). L'unico senza corrispondenza è l'`ai_disabled_forever` per contatto di Amalia.
- **Pausa quando il merchant risponde dal telefono** (Coexistence): 2 h fisse vs N minuti configurabili, stesso effetto.
- **Takeover implicito** quando un operatore scrive, e **handoff silenzioso** configurabile in entrambi.
- **Media pesanti (video/documento) → handoff senza turno AI**, file comunque scaricato per l'inbox. Reloop lo definisce esplicitamente «Amalia pattern».
- **Idempotenza sulla ridelivery del webhook** basata sull'id del messaggio provider (permanente in Amalia via `on_conflict_do_nothing`, a strati in Reloop via `_job_id` ~1h + `find_by_wa_message_id`).
- **Fail-safe che garantisce una risposta su errore LLM**, con passaggio a operatore, in entrambi.
- **Whisper `whisper-1` con `language="it"`** e degradazione a None.
- **Nessun lock per conversazione durante il turno**; nessuno dei due ri-verifica i gate dopo il ritorno dell'LLM (unica eccezione: il claim atomico di Reloop sull'escalation).
- **Nessun timestamp dei messaggi passato al modello**: le date sono caricate e scartate nella serializzazione — il modello non sa se il turno precedente è di 2 minuti o 3 settimane fa.
- **Nessun rate limit / budget LLM per merchant**; nessun calcolo di costo in € dell'inferenza in nessuna delle due.
- **Algoritmo di matching delle correzioni identico** (soglia 0.4, top 2, substring bidirezionale = 1.0, token >2 char) — Reloop lo dichiara come porting.
- **Correzioni create solo dal Playground**, mai dall'inbox su una conversazione reale.
- **Nessuna delle due scrive knowledge base a partire dalle conversazioni reali.**
- **Entrambi appiattiscono i messaggi umani su `assistant`**: il modello non distingue una risposta AI da una scritta a mano.

---

## L'audit del 23/06 è invecchiato così

**Non più vero (Reloop ha colmato):**

- «*single-shot, UNA chiamata LLM, il modello non vede mai l'esito*» (righe 11, 26, 28) → il loop esiste (`R:orchestrator.py:166-197`) con 2 read-tool eseguite mid-turn e default 3 iterazioni. **Riformulare come «loop di grounding limitato alla lettura»**: per tutte le azioni di scrittura l'affermazione resta letteralmente vera.
- «*8 ActionKind*» (riga 26) → oggi sono 10 (`R:orchestrator.py:27-42`).
- «*nessun lookup invocabile: o è nel prompt o non c'è*» (righe 40, 69) → falso: `check_availability` interroga il calendario GHL, `lookup_appointment` legge gli appuntamenti futuri. Vero invece che sono 2 contro ~8 di lettura in Amalia, e che nessuno legge il CRM.
- «*P1: aggiungere azioni di lettura on-demand (check_availability, lookup_contact, get_appointment_status)*» (riga 143) → fatta a metà: `lookup_contact` non esiste.
- «*P0: loop tool-use selettivo, max 2-3 iter*» (riga 132) → implementato, con configurabilità per merchant che l'audit non prevedeva.
- «*allowed_actions esiste solo per le automazioni / ReloopxWave dichiara sempre tutte le azioni*» (righe 46, 61, 73, 113, 151) → il playbook (ADR 0018) filtra sia lo schema mostrato sia le azioni accettate anche nel turno inbound (`R:playbook.py:88-89`, `orchestrator.py:186-189,538-544`). Restano veri: default `None` = tutte, `booking.enabled` non filtra, e le azioni iniettate dal server scavalcano l'allowlist.
- «*ReloopxWave inietta SOLO il punteggio lead, parte ogni turno semi-cieco*» (righe 13, 27, 54) → oggi il prompt porta nome/email con divieto di richiederli, sentiment, policy inline, servizi con UUID, orari strutturati, chiusure, data/ora, correzioni, FSM, rischio escalation, continuità automazione.
- «*le Q&A passano solo per RAG con min_score 0.7 irraggiungibile*» (riga 57) → falso due volte: esiste il ramo KB-inline sotto 10k token, e il default risolto è 0.6 (`R:schema.py:310-314`). Residuo: `default=0.7` come fallback di codice a `R:conversation_service.py:1388`.
- «*manca lo staleness check*» (righe 16, 30, 99, 139, 186) → implementato e configurabile (default 10 min).
- «*NO throttling in uscita*» (righe 14, 29, 84) → esiste, per canale con `Retry-After`. Resta vero che manca il drain a stati con SKIP LOCKED/backoff/dead-letter.
- «*tutti i knob delivery a default no-op, out-of-the-box sembra un bot*» (righe 16, 30, 98, 100-101, 187) → ADR 0013: debounce 8 s, typing on, 2 bolle, delay 1-6 s, jitter 0.25.
- «*su doppio fallimento LLM può restare muto*» (righe 17, 49, 103, 185) → esiste il fail-safe con cortesia + `escalate_human` (`R:conversation_service.py:1635-1670`). **Ma la copertura è parziale**: avvolge solo l'orchestratore, non l'invio a 360dialog.
- «*P0: gerarchia priorità esplicita + blocco Regole ferree merchant-level (bot.hard_rules)*» (riga 136) → implementato con altro nome, `conversation.playbook.directives`. Resta la differenza di posizione (coda vs testa).
- «*P2: esporre few-shot Q/A*» (riga 164) → fatto, `bot.examples`.
- «*Correzioni / feedback loop — vincitore Amalia; ReloopxWave non ha questa superficie*» (riga 117) → falso: tabella `bot_corrections` (migrazione 0024), algoritmo portato, CRUD + UI. L'audit era già internamente incoerente (riga 27 le attribuiva a Reloop).
- «*P2: superficie Correzioni con override prompt per-merchant*» (riga 173) → fatta, e in forma più ampia (`POST /playground/apply`).
- «*E2E 07/07 — max_tokens su gpt-5.x*» → risolto (`R:llm.py:94-99,139-145`). «*rag.min_score 0.7*» → risolto. «*kb_gaps senza dedup*» → risolto (migrazione 0045, `ON CONFLICT DO UPDATE`). «*bot.language ignorato*» → mitigato con la REGOLA ASSOLUTA, non verificato da test.
- «*ReloopxWave: 1 chiamata principale (+1 sentiment nano) deterministica, più prevedibile di Amalia (1-5)*» (righe 42, 44) → **invertito nel caso peggiore**: fino a 6 chiamate sul modello di chat (loop 3 × coherence retry) più coherence check, sentiment, compressione, HyDE, rerank. Ma il turno **modale** è 3 (HyDE/rerank girano solo nel ramo RAG, il rerank solo con >5 candidati sopra soglia, la compressione solo oltre 30 turni). E sul costo per **messaggio ricevuto** Reloop coalesce meglio (8 s vs 3 s, senza bloccare un worker).

**Ancora vero, da non declassare:**

- Guardia anti-allucinazione a livello di codice: **non portata** (righe 12, 41, 133 — P0).
- `payload: dict[str, Any]` non validato (riga 77) — e il problema è più esteso di come descritto: le chiavi non documentate scavalcano la config merchant.
- `propose_slots` non persiste lo slot proposto, `book_slot` si fida del `preferred_start_iso` e su ISO non parsabile ripiega su `_next_business_hour` con orari hardcoded 09:00-18:00 (righe 78, 167). Mitigato ma non risolto da `check_availability`.
- Rinforzo anti-drift in coda: assente in Reloop (riga 59) — e non c'è nemmeno un nome di assistente da bloccare.
- Temperature efficace (righe 47, 63) — **peggiore del descritto**: non è solo che la temp viene scartata su gpt-5, è che nemmeno `max_tokens` viene passato, e la temperatura effettiva cambia a seconda della rotta.
- A/B che bypassa correzioni e sentiment (riga 64) — da riscrivere come «la variante perde business/persona/data/policy/correzioni», non «perde tutto»: i blocchi runtime sopravvivono.
- «*fallback provider rotto (no JSON in fallback)*» (righe 48, 142) → il `response_format` viene ora **passato** ma `AnthropicClient.complete` continua a ignorarlo: **il problema è stato spostato dal chiamante al client, non risolto**. E ce n'è un secondo non noto all'audit (history non normalizzata).
- «*P2: instradare il classificatore via ModelRouter purpose=classification*» (riga 162) → non fatta; due dei quattro `purpose` sono codice morto.
- Cap massimo di attesa del debounce (riga 104, roadmap 154) → non implementato.
- Sanitizzare e troncare il testo outbound (riga 152) → non implementato.
- Bug cross-tenant `apply_status_by_name(merchant_id=None)` (righe 15, 92, 138, 184 — P0) → ancora presente.

**Sbagliato in origine (non stale):**

- «*Amalia inietta nome/storico ordini/spesa/tag*» (riga 13) → vale **solo** per `build_legacy_system_prompt`. Nel percorso AI Engine — quello che il codice chiama «PRODUCTION prompt builder» — il builder non riceve né customer né prodotti né tracking (`A:prompts.py:81-104`), e Amalia parte **più cieca** di Reloop sull'anagrafica.
- «*input_schema validato dal provider*» (riga 77) → l'API non garantisce la validazione dei parametri: in Amalia la validazione reale è codice difensivo manuale in ogni handler.
- «*Amalia ha una rete di sicurezza robusta*» → `retry_ai_response` è codice morto. **Ma il consolidato ha poi sottovalutato l'altro lato**: `safety_net.py` è attivo ogni 5 minuti e ricostruisce dal dominio.

**Non coperto affatto dall'audit** (dimensioni intere mancanti): gate e sicurezza operativa, identità del contatto, arbitraggio AI↔automazioni, retention/GDPR, modello di isolamento dati, catalogo tipi di messaggio in ingresso.

---

## Cosa porterei da Amalia a Reloop

Ordinato per rapporto valore/sforzo. Stime: **XS** < mezza giornata, **S** ~1 giorno, **M** ~2-4 giorni, **L** ~1-2 settimane.

### Valore altissimo / sforzo minimo

1. **Non inviare mai il JSON grezzo al cliente** — nel ramo except di `_parse_structured` (`R:orchestrator.py:610-615`), se `raw` inizia con `{` o `[`, tentare l'estrazione del solo `reply_text` con un regex; in caso di fallimento usare il testo di cortesia + `escalate_human` già esistente. **XS**
2. **`status='pending'` sulla riga assistant + `_mark_failed` sull'eccezione di invio** — il composer lo fa già (`R:routers/conversations.py:275`, `handlers.py:1202-1221`): riusare lo stesso schema in `persist_assistant_message`. Elimina la bolla fantasma in inbox. **XS**
3. **Passare `temperature` e `max_tokens` espliciti** in `_complete` — su gpt-5 la temperature verrà scartata dal client, ma il cap serve, e per i modelli FT la temperature va allineata al baseline. Chiudere anche il gate FT che confronta parametri diversi (`R:evaluate.py:149-155`). **XS**
4. **Regole di formato WhatsApp nel prompt** — le quattro righe di `A:prompts.py:128-139` (no markdown/asterischi, no prefissi, messaggi brevi, «scrivi come una persona vera»). Interagisce con `_BULLET_RE` dello splitter. **XS**
5. **Loggare le tool call** — `logger.info("tool.requested", kind=…, payload=…)` prima dell'esecuzione e `tool.executed` con `ok`/`summary` dopo. `read_tools.py` ha già il logger dichiarato e mai usato. Senza questo, l'intera feature di grounding non è diagnosticabile. **XS**
6. **Ramo `button` nel parser webhook** — `R:integrations/whatsapp/webhook.py:199-204`: aggiungere `elif kind == "button": text = payload["button"]["text"]`. Oggi quei messaggi spariscono. Aggiungere le coordinate nel testo per `location`. **XS**
7. **Gatare `_TOOL_USE_PARAGRAPH` sul tool-use effettivo** — portare `tool_use_enabled` / `max_iterations` dentro `OrchestratorContext` e passarli a `render_schema_hint`, come Amalia fa gatando insieme prompt e tool. Elimina il vicolo cieco deterministico. **XS**
8. **Rinforzo anti-drift in coda + `bot.assistant_name`** — una chiave nella cascata e il blocco di `A:prompts.py:226-233` iniettato come **ultimo** elemento in `_build_messages`, dopo le direttive. Sistema anche il problema del registro ereditato dal composer. **S**

### Valore alto / sforzo medio

9. **Riconciliazione testo↔azione prima dell'invio** — la P0 dell'audit, mai fatta. Regex italiane per «ho prenotato / è confermato / ti ho spostato / annullato» e, se l'azione corrispondente non è in `response.actions`, sostituire con una frase di passaggio. Copre lo scenario di danno massimo. **S/M**
10. **Drenare il buffer debounce DOPO la generazione** (o riaccodarlo sull'eccezione) e avvolgere il ciclo di invio in try/except con cortesia + handoff. Insieme al punto 2, chiude il buco «raffica persa per sempre». **S**
11. **Sanitizzazione del testo e dei parametri template + cap 4096** — port diretto di `A:services/whatsapp/sanitize.py` (con i suoi vettori golden) applicato in `send_text` e in `build_send_components`/`resolve_body_params`/composer. **S**
12. **Normalizzare il telefono sul percorso inbound + match sulle ultime 10 cifre + backfill** — port di `A:services/phone.py:39-96` e del matcher a `A:webhooks/whatsapp.py:719-808`, con un default paese per merchant nella cascata. Senza questo, ogni integrazione CRM produce lead duplicati. **M**
13. **Arbitraggio AI↔automazioni** — non emettere `message.received` quando l'agente risponderà (o marcarlo in modo che il dispatcher lo ignori), e portare `bot.auto_reply_enabled` + `lead.opted_out_at` dentro `_resolve_context` (`R:engine.py:1562-1566`), che è l'unico punto che copre tutti i nodi e tutti i `wait`. **S**
14. **Estendere l'opt-out ai percorsi proattivi** — aggiungere `Lead.opted_out_at IS NULL` alla WHERE di `list_reminder_candidates` (`R:repositories/conversation.py:224-230`) e il gate in `_resolve_context`. Compliance, non feature. **XS/S**
15. **Playground fedele** — passare `tool_executor` e `max_iterations` risolti, più `profile_id` e `variant_id`; e aggiungere i blocchi runtime mancanti (FSM, dati lead, continuità) al ctx del playground. Alternativa più radicale e più sana nel lungo periodo: estrarre da `_generate_and_deliver` una funzione pura «costruisci il ctx» condivisa dai due percorsi, come Amalia ha `_build_system_prompt_and_tools`. **M**
16. **Riconciliatore dallo stato di dominio** — port concettuale di `A:worker/safety_net.py`: un cron che cerca inbound senza outbound successivo entro N minuti (con gate auto-reply e staleness rivalutati) e riaccoda, appoggiandosi all'idempotenza già presente. Copre il buco lasciato dall'assenza di dead-letter. **M**
17. **Gate di abbonamento** — port di `A:services/billing/subscription_guard.py:31-58` (stati + grace 3 giorni), applicato nel turno AI e in `send_and_persist_decision`. Serve prima l'anagrafica abbonamento, che oggi non esiste. **M/L**

### Valore alto / sforzo alto

18. **Portare almeno il booking dentro il turno** — eseguire `book_slot` prima della generazione del testo (o, minimo, un read-back dopo il dispatch che sostituisca la bolla se l'esito diverge), e togliere il template hardcoded `format_booking_confirmation` lasciando che sia il modello a scrivere la conferma. È il vero salto qualitativo, e presuppone il punto 9 come rete. **L**
19. **Passare al function calling nativo OpenAI** per le read-tool — `tools=[...]` con JSON Schema, `tool_calls` correlati, validazione provider-side degli argomenti, `stop_reason` per la terminazione. Elimina in un colpo i punti 3, 10 e 42 dell'elenco differenze. **L**

### Fix interni a Reloop emersi dal confronto (non portati da Amalia)

- `booking.enabled` / `lead_capture.enabled` devono filtrare `allowed_actions`, non solo il testo del prompt.
- `escalate_human` va davvero aggiunto all'allowlist come promesso dal docstring (`R:orchestrator.py:530-541`), e `escalation.enabled=False` va applicato **a monte**, non dopo la generazione.
- `move_pipeline`: allineare la chiave (`stage` nello schema vs `stage_id` nell'handler).
- Non far vincere il payload del modello sulla config del merchant (`calendar_id`, `tags`, `pipeline_id`, `value`, `duration_min`): whitelist o inversione della precedenza.
- `_fetch_slots` deve propagare l'errore (`ok=False` + summary esplicito) invece di ritornare `[]` che diventa «non c'è disponibilità».
- Spostare il check off-hours **prima** dell'assemblaggio del prompt e deduplicare il messaggio fuori orario.
- Saltare RAG/HyDE quando il merchant non ha KB (`kb_tokens == 0`).
- Memoizzare il client OpenAI nel router (o costruirlo una volta per processo).
- Cap all'attesa del debounce (`delivery.debounce_max_wait_s`) ed emissione di typing/read receipt **all'ingresso** della fase 2, non alla fine.
- Correlare i callback di stato a **tutte** le bolle, non solo all'ultima.
- Cablare in UI il toggle `is_active` delle correzioni, `kb_gaps`, `objection-trends` e `lead-scores` — o marcarli come debito; e sistemare i ruoli inesistenti in `require_role`.
- Chiudere o rimuovere il loop `optimal_send_hour`.
- Rimuovere `init_posthog` o aggiungere i `.capture()`, e correggere la CLAUDE.md di conseguenza.
- `apply_status_by_name(merchant_id=None)` sul path webhook: bug cross-tenant aperto da giugno.