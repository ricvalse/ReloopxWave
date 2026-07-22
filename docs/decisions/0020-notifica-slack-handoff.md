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
