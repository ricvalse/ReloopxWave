# ADR 0021 — Catalogo eventi tipato + metriche configurabili

Data: 2026-07-26
Stato: accettato (registry implementato; metriche configurabili = direzione)

## Contesto

Caso d'uso del cliente: "voglio vedere quante volte è successa una certa cosa —
quanti prendono appuntamento (`booking.created`), quante volte un lead è stato
spostato in pipeline (`pipeline.moved`) — in modo **preciso e configurabile**".

Oggi le KPI sono **hardcoded**: `AnalyticsRepository.merchant_kpis` /
`tenant_totals` / `merchants_ranking` contano un insieme fisso di `event_type`
con stringhe cablate a mano (`analytics.py`). Non esiste alcun concetto di
"metrica scegliibile". Peggio: gli `event_type` erano **stringhe libere** sparse
su ~25 call-site di `emit()` e ri-lette per stringa altrove, senza single source
of truth. Questo ha prodotto un **bug reale**: le KPI contavano `reminder.sent`
mentre lo scheduler emette `appointment_reminder.sent` → la metrica
`reminders_sent` era **sempre 0** (merchant e agency).

L'event-store è però già ideale come base: `analytics_events` è append-only con
`event_type` + `properties` JSONB + `subject` + `variant_id` + `occurred_at`,
indici btree su tenant/merchant/event_type/occurred_at, e le query aggregano già
via `GROUP BY event_type`. Manca solo il layer di **definizione** delle metriche.

## Decisione

**Un catalogo tipato degli `event_type` come single source of truth, e la
dashboard configurabile costruita sopra la stessa `GROUP BY event_type` già
esistente — senza nuove tabelle nella V1.**

### Fatto ora (Step 1, questo commit)

1. **Registry `libs/db/src/db/analytics_events.py`**: `EventType(StrEnum)` con
   tutti i ~25 event_type reali (il *valore* è la stringa scritta a DB,
   immutabile: cambiarla romperebbe lo storico append-only), `EventCategory`,
   e `EVENT_CATALOG` con `label`/`descrizione`/`categoria`/`subject_type`/
   `selectable` per ogni evento. `selectable=False` per rollup interni
   (`kpi.daily.*`) e log di sistema (`kb.*`) che non vanno offerti come metrica.

2. **Fix del bug per costruzione**: i reader KPI ora referenziano
   `EventType.APPOINTMENT_REMINDER_SENT` (l'enum), non una stringa libera — un
   typo diventa errore statico, non una metrica a 0. Applicato ai tre punti
   (`merchant_kpis`, `tenant_totals`, confronto SQL in `merchants_ranking`).

3. **Guardrail anti-drift** (`tests/unit/test_analytics_events.py`): un meta-test
   AST-scansiona il sorgente di produzione e pretende che **ogni `event_type=`
   letterale ∈ `EventType`**. È la rete che avrebbe intercettato il bug
   reminder. Più i test di completezza catalogo + filtro `selectable`.

4. **Endpoint `GET /analytics/event-catalog`** (`?selectable_only=`): espone il
   catalogo tipato come vocabolario per il metric-builder del FE.

### Direzione (V1 metriche configurabili, prossimo step)

5. **`MetricDefinition`** = `{id, label, event_type (∈registry), window_days,
   aggregation:'count'}`, salvata come **chiave tipata `dashboard.metrics`** nel
   config cascade (`bot_configs.overrides` / `bot_templates.defaults`), validata
   da `BotConfigSchema` ed esportata via OpenAPI. Eredita gratis cache Redis,
   invalidazione, default d'agenzia. Endpoint generico
   `POST /analytics/metrics/query` che riusa la **stessa** `GROUP BY event_type`
   di `merchant_kpis`, mappando i conteggi sulle metriche scelte. FE: le KPICard
   fisse diventano un `.map()` sulle definizioni risolte.

### Direzione (V2 dimensioni)

6. Per segmentare (per pipeline/servizio/profilo/automazione) servono le
   *properties* come dimensione: aggiungere `pipeline_id` all'emit di
   `pipeline.moved` (oggi non lo porta), thread-are `variant_id`/`profile_id`/
   `automation_id` nelle emit, e un **indice GIN** su `analytics_events.properties`.
   Questo lavoro-dati abilita direttamente la reportistica per-profilo/-automazione
   dell'ADR 0022 (i due assi si saldano qui).

## Conseguenze

- **V1 = solo count grezzi.** I *rate* (es. `booking_rate = booking/lead`) hanno
  denominatori eterogenei non desumibili dai soli `analytics_events` → restano
  metriche dedicate o V2 con denominatore definito.
- **Registry da tenere sincronizzato** coi call-site (che restano stringhe
  libere in emissione): garantito dal meta-test, non da un refactor dei 25 siti.
  Un nuovo `emit("x.y")` non registrato fa fallire il test finché non lo si
  aggiunge al catalogo.
- **Semantica cascade replace-per-leaf**: `dashboard.metrics` a livello merchant
  **sostituisce** quella d'agenzia (non fa merge per-riga). Accettabile per la V1;
  se servisse il merge (merchant che *aggiunge* alle metriche d'agenzia) si
  valuterà una tabella dedicata `metric_definitions` con RLS.
- **Dimensioni per property = full scan** senza GIN → V1 tenuta al solo
  `event_type` (indicizzato); nessun filtro su `properties` finché non c'è GIN.
- **Copertura eventi**: gli appuntamenti creati fuori dal bot (riconciliati da
  `appointment_sync`, `source='ghl'`) non emettono evento → invisibili ai conteggi
  event-based. Fuori scope qui; se servisse, va aggiunto un emit in `appointment_sync`.
- **Nessuna migrazione** nella Step 1. Il nuovo endpoint richiede la rigenerazione
  del client OpenAPI quando il FE lo consumerà.
