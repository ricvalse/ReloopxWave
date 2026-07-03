# Runbook — Eventi webhook GHL: sottoscrizione e verifica

Perché i trigger CRM della lavagnetta (ADR 0016: `crm_lead_created`,
`crm_opportunity_created`) funzionino, GHL deve **inviarci** gli eventi dati.
Gli scope OAuth danno accesso API (pull); i webhook (push) sono una
sottoscrizione **separata e opt-in** nel Developer Portal. Senza, la piattaforma
non riceve nulla e nessun flusso parte.

**Versioning (verificato sulla doc ufficiale, audit 2026-07-03):** le webhook
subscription si modificano **in qualsiasi momento senza creare una nuova
versione dell'app** ("Webhook endpoints and subscribed events can be modified
at any time, even when your app is in a live version" — Webhook Integration
Guide). Ciò che è **bloccato** una volta live sono gli **scope OAuth** (nuova
versione + re-auth): la lista scope completa da azzeccare subito è nel go-live
§4.3.

## 1. Configurazione nel GHL Developer Portal (una tantum, per app)

Su [marketplace.gohighlevel.com](https://marketplace.gohighlevel.com) → My Apps
→ la nostra app:

1. **Webhook URL** (sezione Webhooks / Advanced Settings): deve essere
   `{PUBLIC_API_BASE_URL}/webhooks/ghl/marketplace`. Un solo endpoint per
   tutto: lifecycle INSTALL/UNINSTALL **e** eventi dati. (App Install è
   sottoscritto di default appena la URL è configurata; verifica comunque i
   toggle App Install/App Uninstall.)
2. **Webhook Events — lista da spuntare (8 eventi, audit 2026-07-03)**:
   - `ContactCreate` — trigger "Nuovo lead dal CRM"; porta il telefono, ripara
     la race sull'OpportunityCreate senza phone → **sempre insieme a**:
   - `OpportunityCreate` — trigger "Nuovo lead in pipeline (CRM)" (porta
     `pipelineId`/`pipelineStageId` per il filtro per-pipeline; NON porta il
     telefono del contatto).
   - `ContactUpdate` — sync fill-only nome/email + aggancio `ghl_contact_id`.
   - `OpportunityStageUpdate` — **i move di stage fatti a mano in pipeline**
     (UC-04, mirror `lead.pipeline_stage_id`). ⚠️ Non confondere con:
   - `OpportunityStatusUpdate` — cambio stato open/won/lost/abandoned (mirror
     stage se presente; porta won/lost per futuri KPI/ROI).
   - `AppointmentCreate`, `AppointmentUpdate`, `AppointmentDelete` — oggi
     no-op sicuro nel worker; servono a verificare empiricamente la consegna e
     domani abilitano la cancellazione reminder near-real-time (oggi il poll
     `sync_appointments` ha fino a 30 min di ritardo).

   **NON spuntare** (finché non c'è il codice relativo):
   - `InboundMessage`/`OutboundMessage` — sono l'unico veicolo dei call
     outcome UC-03 (`messageType: CALL` + `callStatus`), MA: (a) volume
     altissimo (portano TUTTI i messaggi di tutti i canali del sub-account),
     (b) il payload non porta il telefono → oggi il takeover si fermerebbe su
     `no_contact_phone`. Attivarli SOLO con il task dedicato (fix
     `handle_call_outcome` via lookup `ghl_contact_id` + filtro non-call
     API-side prima dell'enqueue).
   - `ContactDelete`/`OpportunityDelete` — guard esplicito no-op nel worker;
     sottoscrivibili quando ci sarà il mirror GDPR (erase) vero.
   - `ContactDndUpdate` — utile per sync DND→opt-out (oggi non gestito: il
     bot NON vede il DND di GHL); spuntare insieme al futuro handler.
   - Tutto il resto del catalogo (Invoice*, Order*, Product*, Task*, Note*,
     Location*, User*, Record*, ...) — nessun consumer, solo rumore in coda.
3. Le chiavi di firma NON cambiano: gli eventi dati arrivano firmati con la
   stessa chiave pubblica globale (Ed25519 `x-ghl-signature`, RSA legacy
   `x-wh-signature`) già verificata da `verify_ghl_marketplace_webhook`.
   ⚠️ Un GHL **Workflow** "Custom Webhook" (configurato dall'agenzia) NON è
   firmato → l'endpoint lo respinge 401: non è un canale utilizzabile.
4. ⚠️ Le subscription valgono per le location che hanno l'app **installata**:
   la location del merchant deve avere l'app installata (INSTALL ricevuto) ed
   essere **linkata al merchant** dall'admin UI, altrimenti il worker droppa
   l'evento con `ghl.event.unknown_location`.

## 2. Verifica end-to-end (staging o produzione)

Checklist nell'ordine in cui la catena può rompersi:

1. **L'evento parte da GHL**: nel sub-account della location crea un contatto
   di test **con telefono** e un'opportunity in una pipeline.
2. **Arriva all'API** — log Railway del servizio `API`:
   - `webhook.ghl.marketplace.enqueued` con `event_type=ContactCreate` /
     `OpportunityCreate` → tutto ok fin qui.
   - `webhook.ghl.marketplace.signature_rejected` → chiave firma/rotazione.
   - `webhook.ghl.marketplace.ignored` → payload senza `locationId`/`type`.
   - **Nessuna riga** → la subscription nel Developer Portal manca (o URL
     sbagliato): torna al §1.
3. **Il worker lo processa** — log del servizio `worker`:
   - `ghl.crm_create.processed` con `created=true/false`, `echo`, `emitted=[…]`
     → catena viva; `emitted` non vuoto = evento trigger scritto.
   - `ghl.event.unknown_location` → location non linkata al merchant (admin UI
     → Merchants → collega la location GHL).
   - `ghl.crm_create.no_phone` / `ghl.crm_create.requeued` → il contatto GHL
     non ha telefono (o l'OpportunityCreate ha corso più veloce del suo
     ContactCreate: il retry singolo a 45s di solito ripara).
4. **Il trigger parte** — con un'automazione **abilitata** sul trigger CRM:
   entro ~1 minuto (`automation_dispatch` è un cron al minuto) il flusso parte.
   Skip tipici nei log: `no_context` (nessun canale WhatsApp del merchant →
   conversazione non provisionata), `no_template_outside_window` (il nodo send
   non ha un template **approvato**: un lead a freddo è sempre fuori finestra
   24h), `missing_or_disabled` (flusso non abilitato).
5. **Il messaggio arriva** sul WhatsApp del numero di test.

## 3. Prerequisiti lato piattaforma (per merchant)

- Canale 360dialog connesso (altrimenti niente conversazione → niente emit).
- Template WhatsApp **approvato** selezionato nel nodo `send` del flusso.
- Flusso lavagnetta **abilitato** sul trigger CRM (+ eventuale filtro
  `pipeline_id`/`stage_id`: gli id si leggono dall'URL della pipeline in GHL).
- Per il warm-up conversazionale dopo il template: `bot.auto_reply_enabled=true`
  nel bot config del merchant (default OFF).
