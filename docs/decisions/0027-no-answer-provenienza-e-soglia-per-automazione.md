# ADR 0027 — "Nessuna risposta **a cosa**": provenienza sul trigger, e una soglia per automazione

Data: 2026-08-05
Stato: accettato

> Il numero 0026 è occupato da un ADR su un branch non ancora pushato
> (handoff: istruzioni configurabili). Questo prende 0027 per non collidere.

## Contesto

Il trigger `no_answer` dice "questa conversazione è ferma da X minuti". Non dice
**a cosa** il lead non ha risposto: che l'ultimo messaggio sia stato un template
di primo contatto, una risposta dell'AI o una frase scritta a mano da un
operatore, il trigger li tratta allo stesso modo.

Non è il modello mentale del merchant, che ragiona per campagna: *"chi non
risponde al template del questionario entro 4 ore, sollecitalo — e solo quelli"*.
Sulle conversazioni ferme oltre due ore di Recruiting DM, l'ultimo messaggio è un
template di automazione in 5 casi su 6 e una risposta dell'AI nel sesto: senza un
filtro, il sollecito del questionario parte anche su chi stava conversando con
l'AI e si è distratto.

**Il dato per farlo c'era già.** La migrazione 0047 ha aggiunto l'attribuzione su
`messages` — `automation_id`, `automation_node_key`, `sender_type` — e il
commento nel modello anticipa proprio questo uso: *«serve anche come cancello
deterministico ("sta rispondendo a quel tocco")»*. Il nome del template sta in
`meta.template.name`, scritto da `send_and_persist_decision`. Copertura misurata
su Recruiting DM, ultimi 30 giorni: **81/81 invii di automazione hanno il nome
del template** (100%), mentre `automation_id` ne copre 39/81 (48%, i più vecchi
sono anteriori a 0047). Da qui la scelta di agganciare il filtro al **template** e
non all'automazione di origine: è l'unico asse affidabile anche sullo storico, ed
è quello che il merchant nomina quando descrive il problema.

**Ma la richiesta ne ha fatto emergere una seconda, più grave.** Il
`delay_minutes` non era per automazione: `_threshold_minutes` prendeva il `min()`
fra tutte le automazioni `no_answer` attive del merchant, l'emettitore emetteva
**una volta sola per episodio**, e il dispatcher ventagliava l'evento su tutte le
automazioni sottoscritte al trigger. Con una sola automazione funzionava. Con due
— che è esattamente ciò che serve per avere un sollecito per template — no:

* con ritardi 60 e 240, l'emissione avviene a 60 e **entrambe** partono lì: quella
  da 240 con due ore e mezza di anticipo, cioè ignorando il valore configurato;
* l'ancora dell'episodio (`meta.no_answer_fired_for`, un timestamp solo per
  conversazione) viene bruciata a 60, quindi a 240 non c'è nessuna seconda
  emissione e l'automazione lunga **non partirà mai** al momento giusto.

Il filtro di provenienza da solo sarebbe stato corretto solo nel caso a
un'automazione, cioè quello che non serve a nessuno.

## Decisione

**1. Il nodo trigger `no_answer` accetta `source_template_id`.** Vuoto = nessun
filtro, cioè il comportamento storico, quindi le automazioni esistenti non
cambiano. Valorizzato, l'automazione parte solo se l'ultimo messaggio in uscita
della conversazione è stato mandato con quel template. Nella UI è un campo
`kind: 'template'`, la stessa tendina dei template approvati già usata dai nodi di
invio — il pannello di configurazione la passa a ogni nodo, trigger inclusi,
quindi non serve altro impianto lato frontend.

**2. La provenienza entra nella scansione.** `list_reminder_candidates` fa un
`LEFT JOIN LATERAL` sull'ultimo messaggio in uscita per leggerne
`meta.template.name`, e un join su `whatsapp_templates` (`(merchant_id, name)` è
unico) per risolverlo nell'id che il nodo memorizza. Il candidato porta
`last_outbound_template_id` / `last_outbound_template_name`.

**3. Soglia e ancora diventano per automazione.** `_maybe_emit` non collassa più
niente: cicla sulle automazioni `no_answer` attive del merchant e per ciascuna
valuta il **suo** `delay_minutes`, il **suo** filtro di provenienza e la **sua**
ancora. `meta.no_answer_fired_for` passa da timestamp singolo a mappa
`{automation_id: ancora}`.

**4. L'evento è indirizzato.** Porta `target_automation_id`, e il dispatcher
(`_targeted_at` in `engine.py`) consegna solo all'automazione nominata. Un evento
senza quel campo resta broadcast, come prima.

**Il filtro di provenienza sta nell'emettitore, non in `_trigger_config_match`**
dove vivono quelli dei trigger CRM. La ragione è l'ancora: è l'emettitore a
bruciarla, e scartare a valle timbrerebbe l'episodio per un'automazione che non
doveva nemmeno entrare in gioco, impedendole di partire se in futuro
diventasse pertinente. La divisione dei ruoli è: l'emettitore decide **quali**
automazioni hanno titolo a partire, il dispatcher si limita a instradare.

## Conseguenze

* Un merchant può finalmente avere N automazioni "nessuna risposta", una per
  template, ognuna col suo ritardo. Su Recruiting DM sono cinque pipeline con
  cinque template di primo contatto: prima era impossibile.
* Nessuna migrazione. `meta.no_answer_fired_for` cambia forma ma non ci sono dati
  da convertire — `lead.no_answer` non è mai stato emesso in produzione (ADR
  0025), quindi nessuna ancora esiste. La lettura accetta comunque la forma
  vecchia, interpretandola come jolly valido per ogni automazione
  (`ANCHOR_ANY`), così un deploy parziale o un rollback non perdono
  l'idempotenza.
* Un'automazione con filtro su un template **non ancora inviato** semplicemente
  non parte. È il comportamento voluto, ma va detto nella UI, perché "non
  succede niente" è indistinguibile da un guasto.
* `enabled_trigger_delays` resta invariata e continua a dare il **massimo** per
  merchant allo sweep di chiusura: ora che i ritardi sono davvero per
  automazione, tenere aperta la conversazione fino al più lungo è ancora la
  scelta giusta.
* Il costo della scansione cresce di un LATERAL e di un join, su una query già
  limitata a 500 righe e solo per le conversazioni che hanno superato i filtri.
* Il pavimento di scansione resta il **minimo globale** dei ritardi: corretto,
  perché ogni candidato viene poi rivalutato con la soglia della singola
  automazione.

## Alternative scartate

* **Filtrare per automazione + nodo di origine** invece che per template. Più
  preciso quando lo stesso template è usato da più flussi (succede: su
  Recruiting DM `reloop_first_contact_…ti7z3c` è mandato da due automazioni), e
  permetterebbe di concatenare i flussi. Ma `automation_id` è valorizzato solo
  sul 48% degli invii storici, quindi sulle conversazioni più vecchie il filtro
  non aggancerebbe — e sarebbe un "non parte" silenzioso, il tipo di guasto che
  questa serie di ADR sta cercando di eliminare. Resta la scelta naturale quando
  la copertura sarà completa.
* **Filtrare per `sender_type`** (solo template / solo automazione / solo AI).
  Semplicissimo da spiegare, ma non distingue un template dall'altro, che è
  precisamente la richiesta.
* **Tenere l'emissione unica e ri-verificare la soglia nel dispatcher.** Il
  dispatcher ha `idle_minutes` nelle properties e potrebbe scartare chi non è
  ancora maturo. Ma l'ancora resterebbe bruciata dalla prima emissione e la
  seconda automazione non riceverebbe mai un evento al momento giusto: sposta il
  bug, non lo toglie.
* **Emettere un evento per conversazione e lasciare che ogni automazione tenga il
  proprio stato di run.** È il modello a job differito per conversazione già
  scartato in ADR 0024: più pulito, ma è una migrazione del modello di
  esecuzione, non la correzione di un difetto.
