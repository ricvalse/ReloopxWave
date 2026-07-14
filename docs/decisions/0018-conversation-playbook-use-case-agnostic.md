# ADR 0018 — Conversation playbook: piattaforma use-case-agnostica

Data: 2026-07-14 · Stato: accettato · Contesto: il merchant `wave-recruiting-dm` usa il bot come **promemoria** di un funnel di selezione (ricorda il questionario, dà info su procedura/step). Invece ha condotto un **colloquio di screening** («qual è la tua esperienza più rilevante?», «posso farti 2-3 domande mirate?») ed è dovuto intervenire un operatore umano.

## Contesto

La *forma* della conversazione era cablata come **funnel di vendita**, iniettata a ogni turno **sia inbound sia proattivo (automazioni)**. Le tre sorgenti del comportamento "colloquio" (tutte sempre-on):

1. **FSM di vendita** (`state_machine.py`): `GREETING → QUALIFYING → PITCHING …`, con hint per-turno tipo *"raccogli nome/esigenza/budget/tempistiche, fai una domanda alla volta"* e *"presenta l'offerta"*, iniettati da `conversation_service.py`.
2. **Clausola persona lead-capture** in `build_cascade_system_prompt` (`conversation_service.py:287`/`563`): *"Se mancano informazioni critiche (nome, email, esigenza), chiedile una alla volta"* — per **ogni** merchant.
3. **Manuale azioni** `_RESPONSE_SCHEMA_HINT` (`orchestrator.py`), che pubblicizza `book_slot`/`move_pipeline`/`update_score` a ogni turno.

Le regole comportamentali che il merchant metteva in **knowledge base** arrivavano nel prompt come *"Knowledge base context:"* (materiale di riferimento, bassa salienza) e **perdevano** la gara di salienza contro i tre driver sopra. Inoltre lo scoring (UC-05) e l'avanzamento pipeline deterministico (UC-04) giravano sempre.

## Decisione

Spostare **l'opinione** (non il meccanismo) da codice a **dato risolto dalla cascade**: un *playbook* per-tenant + alcuni flag di capacità discreti, consumati da **un solo assemblatore di prompt** condiviso dai tre percorsi (inbound / automazione / playground). La vendita di oggi è lo `SYSTEM_DEFAULTS`, **byte-identica** (golden test).

Il principio chiave: **non aggiungere una direttiva "più forte"** che spera di gridare più del manuale vendite (era la root cause) — **si RIMUOVONO i competitor** dal prompt quando disabilitati.

### Dato (config cascade, `libs/config_resolver/schema.py`)

- Flag di capacità discreti, risolti **per-foglia** (cascade + lock indipendenti): `scoring.enabled`, `pipeline.auto_advance`, `booking.enabled`, `lead_capture.enabled`, `escalation.critical_keywords`.
- Playbook per-foglia sotto `conversation.playbook`: `mode` (`fsm_legacy` default | `off` | `data`), `goal`, `directives[]`, `actions.enabled[]` (allowlist). Risolto per-foglia (non atomico) così un merchant può override il `mode` ereditando le `directives` dell'agency.

### Motore (`ai_core/playbook.py` → `PlaybookRuntime`)

Risolto una volta per turno e condiviso. Gate applicati:

- `mode == "off"` → **nessun** hint FSM per-turno né transizione di stato (`conversation_service.py`); `data` è Fase 1 (trattato come `fsm_legacy` finché il motore non lo consuma).
- `scoring.enabled == false` → niente `update_score` sintetizzato + il blocco qualificazione **sparisce** dal prompt.
- `pipeline.auto_advance == false` → niente `move_pipeline` deterministico.
- `booking.enabled` / `lead_capture.enabled == false` → in `build_cascade_system_prompt` cadono il blocco "Servizi prenotabili" e la clausola "chiedi nome/email/esigenza".
- `actions.enabled` → `render_schema_hint(allowed)` (`orchestrator.py`) **non menziona** le azioni non permesse; `run()`/`run_proactive()` filtrano gli output; `render_schema_hint(None)` riproduce il manuale completo **verbatim**.
- `directives` (con `goal` in testa) → iniettate come blocco **AUTOREVOLE** ("REGOLE DELLA CONVERSAZIONE…") ad alta salienza; le regole comportamentali non stanno più in KB.
- `escalation.critical_keywords` → override del vocabolario che forza la route di escalation (`_has_critical_objection`).

### UI (configurabile senza deploy)

- Merchant (`bot-config-panel.tsx`): sezione **"Obiettivo & modalità conversazione"** (mode, goal, directives, azioni permesse via multiselect, + i flag di capacità) con pattern Inherited/Customized/Locked; `critical_keywords` nell'escalation.
- Admin (`templates-panel.tsx`): stessi campi come **default di template** + lock per-chiave (`locked_keys`), così l'agency spedisce un preset "recruiting-reminder" e blocca i flag di vendita.

## Conseguenze

- **Zero regressione**: nessun merchant ha override → tutto risolve ai default sales; ogni `if caps.x:` valuta True e prende il ramo di oggi. Provato da `tests/unit/test_playbook_gating.py` (`render_schema_hint(None)` e `_default_system_prompt(True,True)` byte-identici) + 642 unit test verdi.
- **Nessuna migrazione**: il playbook vive in `bot_configs.overrides` / `bot_templates.defaults` (JSONB, RLS gratis). Coerente con [[0014-automazioni-off-di-default]] ("tutto dalla lavagnetta"): l'estende dalle automazioni alla conversazione.
- **Incidente chiuso su tutti i canali** (inbound + automazione + anteprima playground): `wave-recruiting-dm` si esprime con `mode:"off"` + `actions:["escalate_human"]` + direttive, **zero codice**.
- **Limiti onesti**: `ActionKind`, il vocabolario segnali e i read-tool restano codice (l'allowlist **sottrae**, non aggiunge primitive). `render_schema_hint` è superficie di regressione fleet-wide → coperta da golden test. Pesi di scoring per-tenant sullo score **persistito** restano Fase 1.5 (`UpdateScoreHandler` non ancora threddato): in Fase 0 lo scoring è solo on/off. Il `mode:"data"` (FSM data-driven) è Fase 1.
