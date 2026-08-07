# ADR 0025 — "Nessuna risposta" vale anche per chi non ha mai risposto

Data: 2026-08-05
Stato: accettato

## Contesto

ADR 0024 ha corretto l'ordinamento fra i due sweep e si chiude affermando che
«qualunque `delay_minutes` funziona». Non era vero: il trigger continuava a non
partire, per una seconda causa indipendente che stava un livello più in basso.

`ConversationRepository.list_reminder_candidates` filtrava
`last_inbound_at IS NOT NULL`, e `_maybe_emit` usciva subito sullo stesso
controllo. La motivazione scritta nel docstring era: «solo una conversazione con
un inbound vero può ammutolirsi sul lead». Descrive un caso su due — il lead che
risponde e poi sparisce — e taglia fuori esattamente l'altro: **il primo contatto
in uscita a cui non risponde nessuno**, che è ciò che un merchant che fa outreach
chiama "nessuna risposta" nove volte su dieci.

Le due cause si sono mascherate a vicenda per settimane. Fino al 03/08 lo sweep
di chiusura archiviava le conversazioni a 126–180 minuti di inattività, cioè
prima di qualunque ritardo ≥ 120: il gate sull'inbound non poteva emergere,
perché la conversazione spariva dal radar prima di maturare. Sistemato
l'ordinamento, il gate è rimasto — e il sintomo è rimasto identico.

I numeri di Recruiting DM, il merchant che ha segnalato il problema (ritardo
impostato a 240 minuti, automazione abilitata, template approvato, tutto
corretto lato loro):

| conversazioni attive | n | esito |
|---|---|---|
| senza alcun messaggio | 555 | scartate (nessun silenzio da imputare al lead) |
| solo outbound, candidato mai risposto | 11 | **scartate dal gate** |
| con inbound vero | 1 | non ancora matura |

Zero candidati. E su tutta la piattaforma, in tutta la sua storia,
`analytics_events` non conteneva **una sola riga** `lead.no_answer`: il trigger
non è mai stato emesso in produzione.

I test UC-03 non l'hanno intercettato perché il caso "mai risposto" non era
rappresentato: ogni candidato di test nasceva con un `last_inbound_at`
valorizzato, quindi la suite verificava il ramo che funzionava.

Nella UI il trigger si presenta come «Il lead è rimasto in silenzio», senza
distinguere i due silenzi. Un merchant che legge quella frase e imposta 240
minuti si aspetta il comportamento che non c'era.

## Decisione

**1. Il filtro sull'inbound cade.** `list_reminder_candidates` non richiede più
`last_inbound_at IS NOT NULL`. Resta invece `last_message_at IS NOT NULL`: senza
un messaggio nostro non c'è nessun silenzio imputabile al lead — quelle sono
conversazioni create dal CRM su cui non è mai partito niente (le 555 qui sopra),
un problema diverso che va diagnosticato come tale, non sollecitato.

**2. L'ancora di episodio diventa `last_inbound_at or started_at`**
(`_episode_anchor` in `no_answer.py`), usata sia per il gate di idempotenza sia
per `no_answer_fired_for` sia per l'`episode_anchor` portato nell'evento.

Il ripiego **deve** essere un istante immobile, ed è il punto delicato dell'intero
fix. Ancorare a `last_message_at` — la scelta ovvia, visto che è già la misura
dell'inattività — si autoalimenta: il sollecito che l'automazione manda fa
avanzare `last_message_at`, il che riarma il trigger, che dopo altri
`delay_minutes` emette di nuovo, all'infinito, senza che il lead abbia mai fatto
niente. `started_at` è `NOT NULL`, non si muove mai (nemmeno quando
`get_active_or_reopen_latest` riapre una conversazione chiusa) e produce quindi
esattamente un sollecito per lead silenzioso.

Il riarmo resta intatto: se il lead un giorno risponde, `last_inbound_at` diventa
più recente di `started_at`, supera l'ancora bruciata, e il silenzio successivo è
un episodio nuovo.

**3. L'evento porta `never_replied: bool`** fra le `properties`. I due silenzi
sono fenomeni diversi — mai agganciato vs perso per strada — e vanno letti
separatamente nelle statistiche invece di essere sommati.

## Conseguenze

* Un merchant che fa outreach vede finalmente partire il trigger. Su Recruiting
  DM sono 9 conversazioni al primo tick utile, e da lì il regime normale.
* La guardia `_episode_ended` continua a funzionare senza modifiche: per un lead
  mai risposto `last_inbound_at` è `None` e la funzione ritorna `False` (episodio
  mai stale); se risponde durante un nodo `wait`, `last_inbound_at` supera
  l'ancora e la cadenza si interrompe, che è il comportamento voluto.
* **Oltre le 24 ore serve un template approvato.** Non è una novità introdotta
  qui, ma diventa molto più visibile: un lead che non ha mai scritto non ha mai
  aperto una finestra di servizio, quindi `is_within_24h` è falso *da subito* e
  non solo dopo 1440 minuti. Un nodo di invio con `window_policy: 'auto'` e solo
  testo libero salterà con `no_template_outside_window`. Recruiting DM è già su
  `require_template` con template approvato.
* Il pool che compete per il cap di 500 righe/tick si allarga, perché le
  conversazioni senza inbound ora ci entrano. Misurato al momento del fix: 1 → 9
  righe su tutta la piattaforma, contro un cap di 500. Nessun rischio oggi, ma il
  margine va riguardato se il volume cresce — l'ordinamento è per
  `last_message_at` crescente, quindi un merchant con ritardo lungo e molto
  volume potrebbe in teoria affamare un merchant con ritardo corto, le cui
  conversazioni mature sono più recenti e quindi in coda.
* Nessuna migrazione: `started_at` esiste già ed è `NOT NULL`. Le conversazioni
  storiche senza `no_answer_fired_for` sono eleggibili al primo tick, il che è
  corretto (nessuna di loro ha mai ricevuto un sollecito) ma va tenuto presente
  al deploy: il primo giro emette su tutto l'arretrato ancora `active`, che il
  floor di chiusura di ADR 0024 limita comunque a `max(delay) + 30` minuti.

## Alternative scartate

* **Lasciare la semantica com'era e spostare il caso d'uso su un flusso
  `crm_opportunity_created` multi-tocco con nodi `wait`.** Non richiede codice, e
  su Recruiting DM il flusso esisteva già (`74be3a52`, disabilitato). Ma
  significa dire al merchant che il trigger chiamato "Nessuna risposta" non
  serve per i lead che non rispondono, e replicare la cadenza in ogni flusso di
  primo contatto — su Recruiting DM sono cinque, uno per pipeline.
* **Un secondo trigger separato, tipo `no_answer_first_contact`.** Onesto rispetto
  alla distinzione fra i due silenzi, ma raddoppia la superficie di
  configurazione per una differenza che il merchant non percepisce: chi imposta
  "sollecita chi non risponde dopo N minuti" non sta pensando a quante volte il
  lead abbia scritto prima. La distinzione resta dov'è utile — nei dati, via
  `never_replied` — e non nella UI.
* **Ancorare a `last_message_at` invece che a `started_at`.** Descritta sopra:
  produce un loop di solleciti.
