# ADR 0023 — Attribuzione a tre dimensioni ed esiti come righe

Data: 2026-07-28
Stato: accettato (implementato: migrazione 0047 + nodi + API + pagina Statistiche)

## Contesto

Caso d'uso del cliente: il merchant "Recruiting DM" deve vedere **quanti
messaggi sono stati inviati**, **a quanti è stato risposto** e **quanti hanno
detto di aver compilato il questionario**, in una pagina Statistiche
configurabile, divisa per profili, dove sceglie quali "bolle" mostrare.

Audit dello stato prima di questo lavoro:

- **La provenienza di un invio non esisteva da nessuna parte, nemmeno in
  memoria.** `RunContext` — l'oggetto che il motore passa a ogni nodo — portava
  `trigger_type` ma non `automation_id`. `send_and_persist_decision` riceveva un
  `sender_type` stringa piatta. Non c'era modo di dire "questo messaggio l'ha
  mandato il flusso Recruiting DM", né dagli eventi né dai messaggi.
- **`meta.sender_type` era un enum di fatto non tipizzato, e stava già
  divergendo**: il backend ne scriveva 8 valori da 7 file, il union TypeScript
  del frontend ne dichiarava 6 (mancavano `customer` e `agent_action`, quindi un
  messaggio inviato da un'azione dell'agente non matchava nessun ramo della UI).
- **Il 60% del dato "ha risposto" c'era già** (`conversations.last_inbound_at`),
  ma senza attribuzione: non si sapeva *a quale tocco*.
- **`messages` non era append-only** come sembrava: `delivered_at` / `read_at` /
  `failed_at` sono già timestamp scritti dopo l'insert dai callback di delivery.
- **Nessuna tabella `automation_runs`** (il motore è stateless per scelta,
  ADR 0015): non esisteva un posto alternativo dove appoggiare l'attribuzione.

## Decisione

### 1. Completare il pattern di attribuzione che lo schema già usava una volta

`variant_id` (A/B) vive denormalizzato sulle **stesse tre tabelle** —
`conversations`, `messages`, `analytics_events` — perché è la dimensione di
attribuzione: chi/cosa ha determinato quel turno. Profilo e automazione sono la
stessa cosa, e finora uno non esisteva e l'altro era previsto come chiave JSONB.
Diventano colonne accanto a `variant_id`.

Il test per decidere colonna-vs-JSONB: *"ci farò mai un WHERE o un GROUP BY?"*.
Su `automation_id` / `profile_id` / `sender_type` la risposta è sì a ogni render
della pagina. Su `meta.template.components` no — infatti resta dov'è.

### 2. Puntatori vivi con FK, timbri storici senza

`conversations.profile_id` è un puntatore mutabile (il profilo attivo) e ha una
FK. Le colonne su `messages` / `analytics_events` / `lead_outcomes` sono timbri
immutabili scritti all'INSERT e **non hanno FK**: un `ON DELETE SET NULL`
riscriverebbe la storia quando il merchant cancella un'automazione, e la
reportistica di ieri cambierebbe da sola. Un uuid orfano è la risposta onesta
("automazione eliminata"); la perdita del dato no.

### 3. `reply_to_message_id` sull'inbound, non `replied` sull'outbound

L'attribuzione della risposta è **last-touch** e va sul messaggio in entrata:
punta all'ultimo outbound, **e solo se nessun inbound lo ha già seguito**.

- Scritta una volta sola, all'INSERT: nessuna UPDATE retroattiva su una riga
  passata.
- Dice *a quale* invio, non solo *se*.
- Il tempo di risposta arriva gratis (differenza dei due `created_at`) — un
  booleano `replied` l'avrebbe buttato via, e un `replied_at` avrebbe richiesto
  comunque una seconda colonna per sapere a cosa si riferisse.
- La clausola "solo la prima" è ciò che impedisce a un lead che scrive tre
  messaggi di fila di contare tre risposte per un solo invio: senza, il
  reply-rate supera il 100%.

Costo: un JOIN in lettura invece di una colonna già pronta. Accettabile ai
volumi, con `ix_messages_reply_to`.

### 4. Gli esiti sono **righe**, non stringhe

Le bolle "messaggi inviati" e "risposte ricevute" sono strutturali: il loro
vocabolario sta nel codice. Un esito come "ha compilato il questionario" no —
quel vocabolario appartiene al merchant, e un metric-builder ha bisogno di una
tendina da cui sceglierlo.

Se l'esito restasse una stringa libera si riaprirebbe **esattamente** il bug che
ha motivato il catalogo eventi tipato di ADR 0021 (le KPI contavano
`reminder.sent` mentre lo scheduler emetteva `appointment_reminder.sent` → la
metrica è stata zero per mesi). Perciò:

- `outcome_definitions` — il vocabolario, con `key` stabile (ci puntano le righe
  storiche) e `label` rinominabile a piacere.
- `lead_outcomes.outcome_id` — una **FK**, non una key testuale.
- Il nodo `emit_outcome` sceglie da una tendina alimentata dalla stessa fonte che
  legge il metric-builder.

### 5. Un fatto, non un contatore

Il nodo **registra una riga**; la bolla è un `COUNT` su quelle righe. Un
contatore incrementato deriverebbe sotto le ri-esecuzioni del motore stateless,
non sarebbe affettabile per finestra temporale né per profilo, non sarebbe
correggibile e non saprebbe dire *chi*.

L'idempotenza sta nel **database**, non nella logica applicativa: due indici
unique parziali su `cardinality` (`once_per_lead`, `once_per_conversation`)
rendono la seconda emissione un no-op (`ON CONFLICT DO NOTHING`). Serve perché il
motore ri-esegue lo stesso ramo a ogni inbound con la conferma del lead ancora
dentro la finestra di storico letta dall'`ai_check`. Stesso principio del claim
atomico dell'handoff (ADR 0017).

`cardinality` è denormalizzata su `lead_outcomes` perché un indice unique
parziale non può leggere una colonna di un'altra tabella.

### 6. Cancelli deterministici davanti all'`ai_check`

Un flusso su `message_received` con un `ai_check` fa **una chiamata LLM per ogni
messaggio in ingresso del merchant**, non solo per quelli della campagna: quel
trigger non ha nessun filtro naturale. Tre condizioni nuove, tutte a costo zero
(leggono colonne indicizzate o valori già in `RunContext`):

| condizione | legge | serve a |
|---|---|---|
| `conversation_profile` | `conversations.profile_id` | isolare la campagna |
| `last_touch_node` | `messages.automation_node_key` | "sta rispondendo *a quel* tocco" |
| `has_outcome` | indice unique su `lead_outcomes` | escludere chi ha già confermato |

`last_touch_node` è preferibile a `message_contains` quando si riconosce la
risposta a una domanda precisa: se il tocco chiedeva *"hai compilato il
questionario?"*, il lead risponde **"sì"** e nessuna keyword matcherebbe.

Il gate sul profilo esiste in **due posti**, e servono entrambi:

- nel `trigger_config` del trigger (riusa `_trigger_config_match`, già presente
  per `pipeline_id`/`stage_id`): filtra **dentro il dispatcher**, prima di
  accodare il job;
- come condizione nel grafo: visibile sul canvas, permette rami diversi.

La differenza è di costo: con la sola condizione il job parte, costruisce le
dipendenze AI e poi viene scartato. Per lo stesso motivo le dipendenze AI sono
ora **pigre** (`_LazyAiDeps`): prima erano assemblate in cima a ogni run —
inclusa una `list_history(limit=30)` — anche quando i cancelli spengono il ramo
subito dopo.

### 7. Le bolle hanno tre sorgenti, non una

`MetricDefinitionSchema` diventa un'unione discriminata:

- `messages` — **automatica**: filtri strutturali su `direction`/`sender_type`/
  `automation_id`/`profile_id`, più `has_reply`. "Inviati" e "risposti" sono
  **lo stesso insieme letto due volte**, ed è questo che rende il loro rapporto
  un tasso di risposta sensato invece di due misure scorrelate.
- `outcome` — **custom**: richiede una dichiarazione e un cablaggio.
- `event` — il catalogo tipato preesistente.

Un `model_validator` pretende che ogni sorgente porti il proprio riferimento: una
bolla `outcome` senza `outcome_id` è un errore di validazione, non una bolla che
mostra zero per sempre.

La distinzione automatiche/custom **va mantenuta in UI**: se la pagina presenta
tutte le bolle allo stesso modo, il merchant cerca dove cablare "messaggi
inviati" e non lo trova mai.

### 8. `source` sulla riga, non sulla definizione

Un esito accertato da un webhook è un **fatto**; uno dedotto da un `ai_check` è
una **dichiarazione**. Tenere `source` (e `confidence`) sulla singola riga
permette di partire con l'`ai_check` e passare al webhook più avanti senza
perdere lo storico né mescolare in silenzio dati di qualità diversa: la bolla può
dire *"312, di cui 180 verificati"*.

## Alternative scartate

- **Risolvere nel layer eventi** (due nuovi `event_type` `automation.message_sent`
  / `automation.reply_received`): duplicano informazione che sta comunque sulla
  riga `messages` scritta per l'inbox, e richiedono lo stesso threading di
  `automation_id`. Una volta fatto il threading, la colonna è quasi gratis e
  strettamente più utile.
- **`replied_to` booleano** su `messages`: vedi §3.
- **Vocabolario esiti come chiave del config cascade** (`outcomes.catalog`):
  dentro un JSONB non c'è integrità referenziale fra la key emessa e quella
  dichiarata — si rinomina la chiave e si orfana lo storico, si sbaglia a
  digitarla e la bolla resta a zero.
- **Tabella `automation_runs`**: il motore è stateless per scelta (ADR 0015);
  l'attribuzione sul messaggio la sostituisce.

## Conseguenze

- **Il lavoro vero non è la migrazione, è il threading**: `RunContext` →
  `_send_proactive` → `send_and_persist_decision` → `persist_outbound_message`,
  più la risoluzione last-touch sull'inbound.
- **`meta.sender_type` resta scritto in doppio** per una release: il portale
  merchant legge i messaggi direttamente da Supabase (spec 4.4) e in Realtime,
  quindi durante la finestra di deploy un frontend vecchio leggerebbe il JSONB.
  Da rimuovere quando il frontend in produzione legge la colonna.
- **Il backfill di `sender_type` + SET NOT NULL fa una scansione completa** di
  `messages`. Sui volumi attuali sono secondi; a due ordini di grandezza in più
  la migrazione va spezzata (nullable → backfill a lotti → CHECK NOT VALID →
  VALIDATE).
- **Lo storico non è attribuibile**: `automation_id` resta NULL sulle righe
  precedenti alla migrazione. È accettabile — quei messaggi non portavano
  l'informazione da nessuna parte.
- **Cancellare una `outcome_definitions` cancella il suo storico** (CASCADE). Per
  smettere di misurare conservando i dati si usa `enabled=false`.
- **`key` e `cardinality` di un esito non sono modificabili**: la `key` è il
  riferimento stabile dello storico, la `cardinality` governa quale indice unique
  si applica e cambiarla lascerebbe righe vecchie e nuove sotto vincoli diversi.
