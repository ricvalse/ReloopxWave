# ADR 0020 — Notifica Slack all'handoff + SLA-come-evento

Data: 2026-07-20
Stato: accettato

## Contesto

Caso d'uso: "quando una conversazione passa a un operatore umano, avvisa il team
su Slack". Prima di questo ADR **nessun canale di notifica in uscita esisteva**:
l'unico segnale di handoff era il flip di colonne su `conversations`
(`auto_reply=false` + `handoff_*`, migrazione 0025) propagato via Realtime a chi
avesse l'inbox aperta. Se nessuno guardava l'inbox, un cliente arrabbiato restava
in attesa senza che nessuno lo sapesse. Slack (o qualsiasi altro canale) era
greenfield: zero codice, zero dipendenze, zero tabelle di notifica.

L'handoff oggi ha cinque percorsi che convergono sullo stesso stato DB, ma solo
due emettevano l'evento `conversation.escalated` (`actions/escalate.py`,
`conversation_service.py` per i media non gestibili). Il nodo `human_handoff`
della lavagnetta e il takeover manuale dall'inbox non emettevano nulla.

Vincolo esplicito dell'utente: **tenere la logica Slack il più isolata possibile
dalla codebase originale**.

## Decisione

**La notifica è un nodo azione della lavagnetta (`notify_slack`) alimentato da
due nuovi trigger (`conversation_escalated`, `conversation_handoff_overdue`), con
TUTTA la logica Slack in una lib isolata `libs/notifications`. L'SLA è modellato
come evento, non come canale.**

1. **Lib isolata `notifications`** (nuovo membro del workspace uv). Contiene
   client httpx+tenacity per gli *incoming webhook* Slack (stesso split retry di
   `d360_client`: transport→tenacity, 429/5xx→backoff con Retry-After, 4xx→fail
   fast), formatter Block Kit, e `send_slack_notification` best-effort. **Zero
   import da `db`/`ai_core`/`config_resolver`** — dipende solo da httpx/tenacity/
   `shared`, come `integrations`. Prende primitivi (URL + una dataclass
   `SlackNotification`), non oggetti ORM. Il glue (leggere il webhook, costruire
   la notifica dallo stato di dominio) vive nel core, non nella lib. Rimuovere
   Slack = `rm -rf libs/notifications` + revertire ~4 agganci sottili.

2. **Agganci sottili al core** (4): `TRIGGER_TYPES`/`ACTION_TYPES` (+3 stringhe);
   `EVENT_TO_TRIGGER` (+2 mapping) e un ramo `notify_slack` in `_do_action`
   (import lazy della lib, delega); metodi **provider-agnostici**
   `resolve_secret`/`upsert_secret`/`disconnect_provider` nel repository
   `integrations` (nessun metodo `_slack` hardcoded); un endpoint
   `POST /integrations/slack` + blocco status.

3. **Segreto in `integrations`, nessuna migrazione.** Il webhook URL è un
   segreto → tabella `integrations` cifrata AES-256-GCM con `provider='slack'`.
   `provider` è `String(32)` libera senza enum/CHECK, la busta credenziali è
   generica, e la riga è già in `RLS_TABLES_MERCHANT_SCOPED`: l'isolamento
   tenant/merchant è **ereditato** dalle stesse policy che coprono WhatsApp
   (nessuna nuova policy, nessuna migrazione di schema). Il webhook incoming Slack
   porta già il canale, quindi il nodo non ha bisogno di `channel`.

4. **SLA-come-evento** (non hardcoded). Il cron `handoff_sla_sweep` (ogni 5 min)
   trova gli handoff aperti (`handoff_at IS NOT NULL AND handoff_resolved_at IS
   NULL`) più vecchi di `handoff_sla_minutes` (setting, default 15) ed emette
   `conversation.handoff_overdue`, **edge-triggered per episodio di handoff**
   (ancora `handoff_sla_fired_for` in `conversations.meta`, come
   `no_answer_fired_for` — ADR 0015; nessuna migrazione). L'SLA diventa così un
   altro trigger della lavagnetta: l'alert passa per lo **stesso** nodo
   `notify_slack`, zero codice Slack duplicato, e il merchant può anche rispondere
   all'SLA con un `send_message` invece che con Slack. Come no_answer, il cron
   non emette (e non brucia l'ancora) se nessuna automazione ascolta.

5. **Copertura evento chiusa in `_do_human_handoff`.** Il nodo `human_handoff`
   ora emette `conversation.escalated` (prima silenzioso). Per farlo passa da
   `mark_escalated` (non idempotente) a `claim_handoff` (atomico exactly-once,
   ADR 0017): emette **solo se vince il claim**, cioè se il thread non era già in
   handoff. Questo rende il nodo idempotente e chiude il loop di ri-escalation
   che nascerebbe da un'automazione degenere `conversation_handoff_overdue →
   human_handoff` (mark_escalated riavanzava `handoff_at`, ri-armando lo sweep
   SLA all'infinito). Il takeover manuale dall'inbox resta non notificato di
   proposito (l'operatore è già lì).

## Onboarding a step minimi — "Add to Slack" OAuth + auto-seed

Per ridurre al minimo gli step del merchant, la connessione **non** passa per il
copia-incolla di un webhook: si usa **Slack OAuth v2 con scope `incoming-webhook`**
(il pulsante «Aggiungi a Slack»). Con quello scope è Slack stesso a chiedere il
canale durante l'autorizzazione e a restituire un webhook pronto in
`incoming_webhook.url` — zero copia-incolla.

- **Una singola Slack App di Reloop** (in "public distribution"), che ogni merchant
  installa nel proprio workspace. Runbook: `docs/runbooks/slack-app-setup.md`.
- **Punto d'ingresso**: bottone nel portal (merchant loggato). Il flusso riusa il
  pattern OAuth di GHL — `state` firmato (qui HMAC del **merchant_id**, non del
  tenant), callback pubblico senza JWT, `session_scope` service-role. Tutta la
  logica Slack (state signing incluso) vive nella lib isolata `notifications`
  (`oauth.py`), che dipende solo da httpx/shared.
- **Endpoint**: `GET /integrations/slack/oauth/start` (mint state + authorize URL)
  e `GET /integrations/slack/oauth/callback` (verifica state → `oauth.v2.access`
  → `upsert_secret(provider='slack')` → redirect al portal). Il form incolla-URL
  resta come opzione "Avanzato" nel FE.
- **Auto-seed zero-config**: alla **prima** connessione Slack il callback crea (se
  non esiste già) un'automazione `conversation_escalated → notify_slack` **già
  abilitata**, così l'handoff→Slack funziona subito senza toccare la lavagnetta.
  Idempotente (skip se il merchant ha già un'automazione su quel trigger, così un
  reconnect non duplica né resuscita una che aveva disabilitato). È un seed su
  **azione esplicita dell'utente**, non un seeding di default all'onboarding —
  coerente con la deviazione dell'ADR 0015 (niente `ensure_system_automations`).

Step netti per il merchant: **click «Aggiungi a Slack» → scegli canale → Consenti**.

## Conseguenze

- **Isolamento reale ma non totale**: il nodo lavagnetta *deve* toccare 3-4 punti
  del core (taxonomy, dispatch, palette FE), altrimenti il salvataggio fallisce o
  il nodo è un no-op silenzioso. "Staccato" significa agganci sottili + sostanza
  Slack confinata nella lib, non zero agganci.
- **Latenza ~fino a 60s** sul trigger `conversation_escalated` (il dispatcher
  automazioni è a polling ogni minuto, non un bus). Accettabile per un alert
  operativo; se in futuro servisse realtime, il ramo `_do_action` può diventare
  un side-effect diretto al claim.
- **Portabilità**: la lib senza dipendenze interne è candidata allo stack
  condiviso cross-SaaS (Reloop/Amalia) senza rifattorizzazione.
- **Deploy**: nessuna migrazione. Serve `uv sync --all-packages` (già nel
  Dockerfile di worker e api) + `uv.lock` aggiornato committato + rigenerazione
  del client OpenAPI (fatta). Setting opzionale `handoff_sla_minutes`.
- **Payload evento disomogeneo** (bug preesistente, fuori scope qui): il
  percorso LLM include `summary` ma non `variant_id`, il media path il contrario
  — l'A/B test perde l'attribuzione variante sugli handoff da azione LLM.
- **Accettato: `notify_slack` richiede un canale WhatsApp attivo.** `automation_run`
  esce con `skipped: no_channel` (`workers/automation/engine.py:329`) prima di
  percorrere il grafo, quindi un'automazione di sola notifica non parte per un
  merchant senza WhatsApp collegato. **Scelta deliberata** (verificata in test
  2026-07-20): l'handoff nasce da una conversazione WhatsApp, quindi un merchant
  senza canale non ha handoff da notificare. Conseguenza da conoscere: un merchant
  senza WhatsApp attivo non riceve alert e lo skip è silenzioso (solo log).
- **Rischio noto — replay dello `state` OAuth (difesa in profondità).** Lo `state`
  è un token HMAC stateless (firma merchant_id + nonce + exp): la firma impedisce
  di *forgiare* uno state per un merchant arbitrario, ma il nonce non è consumato
  one-time né legato alla sessione del browser, quindi entro il TTL (600s) uno
  state legittimo *intercettato* (es. dagli access-log del proxy, dove finisce in
  query string della callback) potrebbe essere abbinato a un `code` di un altro
  workspace e legare il webhook dell'attaccante al merchant vittima. Precondizione
  non banale (accesso ai log = posizione privilegiata). **È lo stesso pattern del
  flusso OAuth di GHL già in produzione** (`integrations/ghl/oauth.py`), non una
  regressione introdotta qui. Hardening consigliato **a livello di piattaforma
  per entrambi i flussi**: consumo one-time del nonce (Redis `SET NX` con TTL) o
  binding a un cookie di sessione. Non applicato solo a Slack per non divergere
  dal pattern GHL.
