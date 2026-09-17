# ADR 0034 — `message_contains`: niente parole chiave vuote, accenti equivalenti; pattern "Nuovo lead" + Thank-You-Page

Data: 2026-09-15
Stato: accettato

## Contesto

Il merchant Ghilea ha segnalato due problemi sulla propria automazione "Nuovo
lead" (trigger `crm_opportunity_created`):

1. Quando l'automazione manda una domanda e il lead risponde con una conferma
   ("Sì, certo"), il flusso non prosegue.
2. Serve instradare diversamente un lead che arriva dalla Thank-You-Page con un
   messaggio WhatsApp precompilato ("Ciao, ho visto la promo volevo fissare
   l'appuntamento"): saltare il benvenuto generico e rispondere subito con il
   template di prenotazione.

Il punto 1 è **già** documentato da `tests/unit/test_automation_ghilea_repro.py`
(aggiunto nel commit 15c833d, 2026-07-22), che riproduce deterministicamente il
grafo reale letto da produzione (`automation_flows`
`34094b0f-e86d-41ef-b47b-5ce0548ae422`, conversazione
`ba24e5b6-882c-4296-9a85-280d50fd2450`, 21/07). La causa non è un bug puntuale:
`_walk` (`workers/automation/engine.py`) è una **BFS a passata singola**
eseguita al momento del trigger. L'unica primitiva di pausa è il nodo `wait`
(deferral di N minuti, che ri-esegue `_resolve_context` alla ripresa); non
esiste un resume guidato dall'inbound del lead (`wait_for_reply`). Il grafo di
Ghilea non ha nodi `wait` fra il primo invio e i nodi che leggono la risposta
del lead (`ai_check`, `condition_group` con clausole `message_contains`), quindi
quelle condizioni vengono valutate a t=0, quando il lead non ha ancora scritto
nulla — finestra 24h chiusa, `last_message=""`. Solo il primo nodo (il template
di benvenuto) parte; tutto il resto muore in silenzio, senza log e senza
deferral, quindi nessun job riprenderà mai il grafo qualunque cosa il lead
risponda dopo.

La direzione già presa dal commit 15c833d per questo limite è **agent-first**:
l'automazione fa **solo il primo tocco**; il resto della conversazione
(preferenze, proposta slot, conferma booking) lo gestisce l'agente
conversazionale normale (UC-01), che dispone delle stesse action (`propose_slots`,
`book_slot`, …) su ogni turno in ingresso — non solo dentro un nodo `ai_reply`
dell'automazione. Quel commit ha già sbloccato la parte agente (grounding
temporale nel prompt, date leggibili, persistenza degli invii delle action) e
annota esplicitamente il repro come "da riscrivere quando arriverà
`wait_for_reply`" — cioè: costruire un vero motore a stati con resume
sull'inbound è lavoro futuro non deciso qui, non lo scope di questo giro.

Durante l'analisi sono emersi due difetti reali e generali nella condizione che
un merchant userebbe per riconoscere "il lead ha risposto affermativamente" o
"il messaggio contiene X" — indipendenti dal grafo specifico di Ghilea e
rilevanti anche per il pattern del punto 2:

* **`message_contains` con parole chiave vuote passa la validazione e fallisce
  chiuso per sempre.** `_condition_config_errors` validava già `ai_check`,
  `has_outcome`, `conversation_profile`, `last_touch_node` (e le clausole
  equivalenti dentro `condition_group`) ma dichiarava esplicitamente
  `message_contains` "lax, matching the existing behaviour". A runtime,
  `any(k and k in text for k in [])` è sempre `False`: un nodo salvato con la
  lista keyword vuota (o mai compilata) non emette nessun errore in nessun
  momento — non al salvataggio, non all'esecuzione — e il ramo "vero" non
  scatta mai. Esattamente il sintomo riportato: "il flusso non prosegue",
  senza traccia diagnosticabile da nessuna parte.
* **Il confronto non normalizza gli accenti.** L'italiano scritto su WhatsApp
  oscilla fra "si" e "sì" a seconda di tastiera/autocorrezione/fretta di chi
  scrive. Un merchant che configura una sola grafia (tipicamente "si", più
  comoda da digitare) perde silenziosamente le risposte con l'altra — la
  classe di errore più probabile dietro "risponde 'Sì, certo' e non passa".

## Decisione

### 1. `message_contains` richiede almeno una parola chiave non vuota

`_condition_config_errors` (sia sul nodo atomico sia sulla clausola dentro
`condition_group`) ora rifiuta al salvataggio una lista keyword assente, vuota
o fatta di soli spazi — stesso principio già applicato a `last_touch_node` /
`has_outcome` / `conversation_profile`: un riferimento che fallirebbe muto a
runtime è un errore al momento della validazione, non un comportamento "lax".
Non è una modifica retroattiva sui dati: un'automazione già salvata con
keyword vuote continua a esistere ed essere eseguita (fallendo chiusa come
prima); il nuovo controllo interviene solo al **prossimo salvataggio** dal
canvas, che è anche il primo momento in cui il merchant può correggerlo.

### 2. Il confronto ignora maiuscole/minuscole e accenti

`evaluate_condition("message_contains", …)` normalizza sia il testo del
messaggio sia ogni keyword con un fold NFKD che rimuove i diacritici
(`_fold_text` in `ai_core/automations.py`) prima del confronto per
sottostringa. "si"/"sì", "perche"/"perché", "piu"/"più" ora matchano a
prescindere da quale grafia ha configurato il merchant o scritto il lead.
Prima si comparava solo `.lower()`.

### 3. Pattern raccomandato per "Nuovo lead" + Thank-You-Page (nessun nuovo nodo)

Per il punto 2 non serve un nuovo tipo di nodo né un campo di configurazione
sul trigger: l'ADR 0014 vieta contenuto/logica cablati fuori dalla lavagnetta,
e il pattern è già esprimibile con le primitive esistenti, purché rispetti lo
stesso vincolo del punto 1 — **niente lettura del messaggio del lead senza un
`wait` prima**, perché `lead.crm_created`/`crm_opportunity_created` (ADR 0016)
non porta alcun riferimento al messaggio WhatsApp: il webhook GHL e il webhook
360dialog sono due eventi indipendenti e in corsa fra loro. Un messaggio
precompilato inviato "contestualmente" al click sulla Thank-You-Page può
arrivare prima o dopo che il dispatcher (cron a 1 minuto,
`_DISPATCH_LOOKBACK_S=120`) legga l'evento CRM.

Grafo consigliato:

```
[trigger: Nuovo lead dal CRM]
        │
     [wait: 1-2 minuti]                  ← lascia atterrare l'eventuale
        │                                  inbound WhatsApp prima di leggerlo
   [Se: Messaggio contiene "appuntamento"/"prenotare"/…]
     │true                     │false
[send_template: appuntamento]  [send: benvenuto normale]
```

Il nodo `wait` rende affidabile la lettura: alla ripresa, `automation_run`
ri-esegue `_resolve_context`, che rilegge `last_message` da
`_latest_inbound_text` sullo stato **fresco** — non quello fotografato al
trigger. Verificato end-to-end (senza DB/LLM) in
`test_crm_trigger_wait_then_defers_before_checking_the_reply`,
`test_crm_trigger_routes_straight_to_booking_when_reply_already_landed` e
`test_crm_trigger_falls_back_to_welcome_when_no_appointment_ask`
(`tests/unit/test_automation_engine_walk.py`). Lo stesso pattern (`wait` prima
di qualunque nodo che legga la risposta del lead) è la correzione minima anche
per il punto 1: un `condition_group`/`message_contains` (o meglio,
`last_touch_node` — vedi sotto) dopo un `wait` di durata ragionevole valuta
sempre uno stato in cui il lead ha avuto il tempo di rispondere.

### 4. `last_touch_node` resta la condizione da preferire a `message_contains` per "sta rispondendo a quella domanda"

Non cambiato qui, ma vale la pena ribadirlo: `message_contains` richiede di
indovinare ogni sinonimo/grafia di una risposta affermativa ("sì", "si",
"certo", "ok", "va bene", "perfetto", …) — il fold sugli accenti riduce ma non
elimina il rischio di un sinonimo mancante. `last_touch_node` (ADR 0047,
`ai_core/automations.py:414`) confronta invece "l'ultimo messaggio in uscita
del thread è partito da questo nodo", che è vero indipendentemente da *cosa*
il lead ha risposto. Per un ramo binario "il lead ha risposto (qualunque cosa)
a questo tocco", preferirlo a un elenco di keyword.

## Conseguenze

* 1120 unit test verdi (57 nella somma dei tre file di automazioni toccati o
  coinvolti, +6 nuovi: 3 di validazione/accenti in `test_automations_graph.py`,
  3 sul pattern Thank-You-Page in `test_automation_engine_walk.py`; i 5 test del
  repro Ghilea restano verdi e non sono stati toccati). ruff/ruff format puliti
  sui file modificati;
  mypy invariato (i 2 errori pre-esistenti in `automations.py` — import-untyped
  su `db.models.automation` e un indice `Any | None` — non sono stati toccati,
  CLAUDE.md vieta le riformattazioni collaterali).
* Nessuna migrazione: nessuna colonna nuova, nessun nodo/tipo nuovo nel
  `NODE_KINDS`/`CONDITION_TYPES` di `db/models/automation.py`.
* **Resta da fare, fuori dallo scope di codice di questo ADR** (richiede
  accesso al DB di produzione/UI merchant, non disponibile da questa sessione):
  * Ridisegnare il grafo `34094b0f-e86d-41ef-b47b-5ce0548ae422` di Ghilea:
    tenere solo trigger → primo template, e lasciare il resto della
    conversazione (preferenze, slot, conferma) all'agente — la direzione già
    presa da 15c833d. Se il merchant vuole comunque un ramo esplicito
    "risposta ricevuta" nella lavagnetta, applicare il pattern del punto 3
    (`wait` + condizione) invece di condizioni-a-t=0.
  * Lo stesso grafo ha un secondo bug, di **configurazione** non di codice,
    fotografato da `test_ghilea_n5_routes_pomeriggio_to_the_morning_branch`: il
    nodo `n5` è un OR fra "contiene Mattina" e "contiene Pomeriggio", quindi chi
    risponde "Pomeriggio" soddisfa comunque l'OR e finisce sul ramo `true` =
    slot di mattina. Va sostituito con un singolo `message_contains` (una sola
    parola chiave) sui due rami, non con un `condition_group`.
  * Costruire concretamente il ramo Thank-You-Page del punto 3 sulla lavagnetta
    del merchant, con le parole chiave reali che la sua landing page usa nel
    messaggio precompilato.
* Non fatto, deliberatamente: nessun nuovo nodo `wait_for_reply` o motore a
  stati con resume sull'inbound. È il limite strutturale reale dietro il punto
  1, ma è un cambio di architettura (nuova primitiva, stato di run persistito,
  interazione con dedup/episode-anchor) che merita una decisione a sé — non un
  quick fix dentro questo ADR — ed è esplicitamente rimandato dal commit
  15c833d ("da riscrivere quando arriverà `wait_for_reply`").
