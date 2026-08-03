# ADR 0023 — Handoff: l'episodio si chiude, e chi parla da solo lo rispetta

Data: 2026-07-29
Stato: accettato
Estende: ADR 0017 (handoff exactly-once), ADR 0020 (notifica Slack all'handoff)

## Contesto

L'ADR 0017 ha reso l'**apertura** dell'handoff exactly-once: claim atomico
(`UPDATE … WHERE auto_reply = true`), un solo messaggio al cliente, un solo
evento. Quella parte regge — i test di regressione lo dimostrano ancora.

Un audit del flusso end-to-end (2026-07-29) ha trovato che tutto il resto del
ciclo di vita non era stato progettato con lo stesso rigore. Quattro difetti
reali, tutti fuori dal nucleo del claim:

1. **Il bot taceva, le automazioni no.** `run_ctx.ai_paused` era controllato
   solo dentro `_do_ai_reply`. Un flusso con un nodo `send`/`send_template`
   scriveva al cliente mentre l'operatore aveva il thread in mano. Peggio,
   `list_reminder_candidates` non filtrava affatto sull'handoff: il cron
   `followup_no_answer` mandava «ci sei ancora?» a un cliente con cui un umano
   stava parlando al telefono.
2. **Il rilascio esisteva in due versioni, una rotta.** `POST /ai-resume`
   chiudeva l'episodio correttamente; l'interruttore "Bot" dell'inbox scriveva
   `auto_reply` **diretto su Supabase**, lasciando `handoff_resolved_at` NULL.
   Il thread rispondeva ma restava "in handoff" per il gate `ai_paused` e per lo
   sweep SLA: automazioni proattive mute per sempre su quel thread e alert che
   nessuno poteva chiudere.
3. **La FSM non tornava indietro.** `ESCALATED` è terminale sticky e il resume
   non lo toccava: il bot riprendeva a rispondere con un system prompt che gli
   ordinava «la conversazione è in carico a un operatore, non rispondere
   automaticamente».
4. **Il pool degli handoff aperti non si drenava.** Niente li risolve d'ufficio,
   `close_idle_active` chiude la conversazione senza toccare le colonne
   handoff, e `list_overdue_handoffs` non filtrava su `status`. Ogni escalation
   mai risolta restava candidata per sempre; con `ORDER BY handoff_at LIMIT 500`
   e l'ancora non bruciata quando nessuno ascolta, le righe vecchie occupavano
   la testa della coda e affamavano gli alert freschi.

Sul lato Slack (ADR 0020) l'audit ha trovato che la catena funziona, ma che si
poteva restare "Connesso" senza ricevere nulla: il salvataggio manuale del
webhook non creava l'automazione, il seed si autoescludeva se esisteva già un
qualsiasi flusso su `conversation_escalated`, e un flusso di sola notifica non
partiva affatto senza un canale WhatsApp risolvibile.

## Decisione

**1. L'handoff è un episodio con due estremi simmetrici.** `claim_handoff` lo
apre, `resolve_handoff` lo chiude — e chiude *tutti* i campi che il claim aveva
toccato: `auto_reply`, `ai_disabled_until`, `handoff_resolved_at` e
`current_state`. Il claim salva in `meta.state_before_handoff` lo stato del
funnel prima del takeover; il resolve lo ripristina (mai `ESCALATED` su se
stesso, fallback `QUALIFYING`). Nessun client scrive più `auto_reply` da solo:
l'interruttore dell'inbox passa da `POST /ai-resume` e dal nuovo
`POST /ai-takeover`.

**2. Un solo posto decide chi può parlare al cliente.** `_CUSTOMER_FACING_NODES`
(`ai_reply`, `send`, `send_template`, `send_message`) è controllato in
`_do_action`, non dentro un singolo handler. I nodi interni — `notify_slack`,
`set_lead_field`, `human_handoff` — continuano a girare: sono il modo in cui
l'operatore viene a sapere del thread. In SQL, `_open_handoff()` è l'unica
definizione di "un umano possiede questo thread", usata sia dagli emettitori
(`list_reminder_candidates`, candidati dormant) sia dallo sweep.

**3. Il takeover manuale non è un handoff in attesa.** `claim_manual_handoff`
apre l'episodio ma brucia in anticipo l'ancora SLA: lo sweep chiede «qualcuno
l'ha preso in carico?» e qui la risposta è sì per costruzione — l'operatore è
quello che l'ha aperto. Senza questo, ogni risposta manuale programmava un alert
«handoff in attesa» contro la persona che lo stava gestendo.

**4. La finestra SLA è limitata, in entrambe le direzioni.** `status = 'active'`
più `max_age_hours = 24`. Un handoff più vecchio di un giorno è un problema di
triage, non una violazione di SLA; e il limite chiude anche la raffica
retroattiva che colpiva chi attivava l'automazione overdue mesi dopo.

**5. Un solo percorso di claim per l'azione `escalate_human`.**
`TurnContext.handoff_claimed` dice all'handler se il chiamante ha già vinto il
claim (percorso inbound, che deve claimare *prima* di inviare). Se non l'ha
vinto — nodo `ai_reply` proattivo — è l'handler a claimare, e se perde esce
senza emettere. Il `mark_escalated` incondizionato che l'handler chiamava dopo
il claim è sparito: riscriveva `handoff_at` e azzerava `handoff_resolved_at`,
cioè poteva annullare il resolve di un operatore.

**6. `escalation.enabled = false` non sopprime l'allarme di un takeover
avvenuto.** Su errore LLM la reply-policy claima comunque come rete di
sicurezza; prima l'handler usciva subito e nessuno veniva avvisato, lasciando un
thread muto per sempre e un cliente con in mano un messaggio che gli prometteva
un operatore. Ora la disabilitazione blocca solo le escalation che l'handler
inizierebbe lui.

**7. Slack: "Connesso" deve significare "arriveranno notifiche".** Il seed
dell'automazione gira anche sul salvataggio manuale del webhook, e la sua
condizione di idempotenza guarda la presenza di un nodo `notify_slack`, non del
solo trigger. Il canale WhatsApp è richiesto solo se il flusso contiene un nodo
che parla al cliente. Un 4xx definitivo di Slack porta la riga `integrations` a
`status='error'` con `meta.last_error`, invece di lasciare una card verde su un
webhook morto. Il nodo non manda alert per handoff già risolti e non descrive
come handoff ciò che gira sotto un trigger diverso.

**8. La chiave di dedup si prende corta e si promuove a fatto.** `_DEDUP_PENDING_TTL`
(300s) durante il run, 24h solo dopo che il walk è tornato. Prima un run morto a
metà bruciava la propria chiave e la notifica spariva per un giorno, in silenzio.

## Conseguenze

- Il ciclo può ripetersi pulito: dopo un resolve, `auto_reply` è di nuovo `true`,
  quindi un claim successivo vince, `handoff_at` avanza oltre l'ancora e sia lo
  sweep sia il gate proattivo si ri-armano da soli.
- Costo accettato: un flusso proattivo che parte mentre l'operatore ha il thread
  viene **saltato**, non rinviato. Il trigger è edge-triggered e non torna: se
  serve un recupero a valle del resolve, va modellato sulla lavagnetta.
- Costo accettato: un handoff dimenticato per più di 24h non genera più alert.
  È deliberato — l'alert serve a prendere in carico in fretta, e ripeterlo per
  settimane su thread chiusi era il motivo per cui il canale diventava rumore.
- `handoff_summary` non è più `coalesce`: un nuovo episodio ha un brief nuovo o
  nessun brief, mai quello vecchio (che finiva nell'alert Slack sbagliato).
- Test: `test_automation_takeover_gate.py` (nodi customer-facing gated,
  `notify_slack` no), nuovi casi in `test_escalate.py` (claim perso, claim già
  vinto dal chiamante, escalation disabilitata ma takeover avvenuto) e in
  `test_notify_slack_node.py` (handoff già risolto, trigger non-handoff).
