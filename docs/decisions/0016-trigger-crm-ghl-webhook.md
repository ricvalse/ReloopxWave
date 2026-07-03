# ADR 0016 — Trigger CRM dalla lavagnetta: lead/opportunity creati in GHL

Data: 2026-07-03
Stato: accettato

## Contesto

Caso d'uso cliente: "un nuovo contatto entra nella pipeline GHL → parte
un'automazione WhatsApp di primo contatto (template) che scalda il lead e lo
porta a prenotare". Prima di questo ADR la catena non esisteva in nessun
anello: `handle_ghl_event` era uno specchio fill-only su lead **già esistenti**
(nessuna creazione, nessun evento emesso), il vocabolario trigger della
lavagnetta non aveva nulla di CRM-originato, e il motore automazioni non sapeva
inviare a un lead senza conversazione (skip `no_context`).

## Decisione

**Gli eventi di creazione GHL (match esatto `ContactCreate` /
`OpportunityCreate`) diventano emettitori di trigger, nella direzione ADR 0015
(evento in `analytics_events` → `automation_dispatch` → flusso lavagnetta).**

1. **Origination con match esatto.** Solo i due Create possono creare stato
   (get-or-create del Lead); gli altri eventi restano sync substring fill-only.
   Un tipo evento GHL futuro non potrà mai creare righe per sbaglio.
2. **Normalizzazione telefono** (`shared.normalize_phone`): GHL manda E.164
   ("+39 333…"), i lead WhatsApp sono cifre nude ("39333…"). Ridotte entrambe a
   cifre (con strip del prefisso internazionale "00") le due sorgenti chiavano
   lo stesso lead — è il perno anti-duplicati. Numeri senza prefisso paese non
   sono riparabili in V1 (nessun country default per merchant) e passano as-is.
3. **Conversazione provisionata subito** (stesso pattern del takeover
   chiamata-fallita): il motore automazioni risolve il contesto d'invio dalla
   conversazione; senza, il run skipperebbe `no_context`. Il lead a freddo è
   fuori finestra 24h per costruzione → `decide_outbound` esige un template
   approvato: il vincolo compliance del primo messaggio è già nel motore.
4. **Due eventi, due trigger**:
   - `lead.crm_created` → trigger `crm_lead_created` — emesso **solo alla
     creazione effettiva** del Lead (flag `created` di
     `upsert_by_phone_flagged`).
   - `opportunity.created` → trigger `crm_opportunity_created` — emesso a ogni
     OpportunityCreate non-echo, con `pipeline_id`/`stage_id` nelle properties.
     È il trigger del caso d'uso cliente ("entra in QUELLA pipeline"): il
     dispatcher filtra su `trigger_config.pipeline_id`/`stage_id` (vuoto = ogni
     pipeline). Emesso anche per lead già noti: un lead WhatsApp che il
     merchant mette in pipeline a mano È un ingresso in pipeline; le condizioni
     del flusso decidono cosa farne.
5. **Guardia anti-echo**: il bot stesso scrive opportunity su GHL
   (move_pipeline/booking stashano l'id creato in
   `lead.meta.ghl_opportunity_id`); l'eco del webhook con lo stesso id non
   emette nulla. Resta una finestra teorica se l'eco arriva prima del commit
   del nostro turno — accettata in V1 (il dedup `_job_id` a monte copre le
   re-delivery, non questo).
6. **Race ContactCreate/OpportunityCreate**: un OpportunityCreate può arrivare
   prima del sibling ContactCreate e non portare telefono → re-enqueue singolo
   con defer 45s, poi drop (`no_phone`). Non si fetcha il contatto dall'API GHL
   in V1 (eviterebbe il drop ma porta in un handler webhook la gestione
   refresh-token del client GHL).
7. **Gate opt-out**: nessuna emissione per lead con `opted_out_at` valorizzato.
   L'opt-in per messaggi marketing resta NON modellato (invariato, va gestito a
   processo col cliente).

## Conseguenze

- Nessuna migrazione DB (`trigger_type` è `String(64)` senza CHECK; la
  validazione è applicativa su `TRIGGER_TYPES`), nessuna rigenerazione client
  API (il catalog resta `list[str]`).
- La sottoscrizione agli eventi (`ContactCreate`, `OpportunityCreate`) nel GHL
  Developer Portal è un **prerequisito esterno**: senza, GHL non invia nulla.
  Procedura e verifica: `docs/runbooks/ghl-webhook-events.md`.
- Il primo messaggio del flusso a freddo DEVE essere un nodo `send` con
  template approvato; perché il lead che risponde riceva il warm-up serve
  `bot.auto_reply_enabled = true` per il merchant (default OFF).
- Nessun pacing sui tier di messaggistica Meta: un import massivo di contatti
  in pipeline genera un burst di template (solo rate-limit per canale). Rinvio
  consapevole a V2.
