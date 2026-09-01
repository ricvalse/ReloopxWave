# ADR 0030 — Gli orari valgono anche per le automazioni, e fuori orario si accoda

**Stato:** accettata — 2026-09-01
**Contesto:** UC-01, UC-02, CC-CONFIG, lavagnetta automazioni
**Completa:** ADR 0028 §5 (la chiave `schedule.apply_to_automations` era definita,
risolta ed esposta in UI, ma il gate non era cablato: accenderla non faceva nulla)

## Il problema

`schedule.mode` dice quando l'assistente può parlare. Da ADR 0028 quel vincolo
si applicava **solo al turno in ingresso** (UC-01): una domanda arrivata di
notte veniva marcata e ripresa alla riapertura.

Gli invii **proattivi** — promemoria, follow-up "nessuna risposta",
riattivazione dormienti, qualunque nodo `send` della lavagnetta — continuavano
invece a partire a qualunque ora. Il risultato dal punto di vista del cliente è
il peggiore dei due mondi: scrive alle 23:00 e riceve "ti risponderemo domani",
poi alle 03:00 gli arriva il promemoria automatico. Il negozio è chiuso per le
domande e sveglio per la pubblicità.

ADR 0028 §5 aveva già previsto la chiave di opt-in e lasciato aperta una
domanda: *rimandare o saltare?*

## Le decisioni

### 1. Si rimanda, non si salta

Un invio saltato è perso per sempre, e nessuno se ne accorge: il promemoria
dell'appuntamento di domani semplicemente non arriva. Un invio rimandato arriva
in ritardo, il che è il comportamento che il merchant ha chiesto quando ha
scritto "rispondi solo 09:00-18:00".

Vale per tutti e quattro i nodi customer-facing (`send`, `send_message`,
`send_template`, `ai_reply`). I nodi interni — `notify_slack`,
`set_lead_field`, `emit_outcome`, `human_handoff`, le condizioni — **continuano
a girare fuori orario**, esattamente come già fanno sotto il gate takeover
(`engine.py`, `_CUSTOMER_FACING_NODES`): sono il modo in cui un operatore viene
avvisato, e avvisarlo alle 3 di notte di un lead caldo è precisamente il punto.

`send_template` è incluso benché il template esista per parlare **fuori** dalla
finestra di servizio WhatsApp: quella finestra è una regola di piattaforma sul
consenso, questa è una regola del merchant su quando il telefono del cliente
suona. Sono due vincoli diversi e vanno applicati entrambi.

### 2. Il ramo si ferma sul nodo, e riprende **da quel nodo**

Il gate non sta dentro `_do_action` ma dentro `_walk`, accanto al nodo `wait`,
e per lo stesso motivo: `_do_action` che ritorna `False` lascia comunque
proseguire la coda sui successori. Un invio "rimandato" che però fa correre il
resto del ramo verrebbe eseguito due volte — una adesso per i successori, una
alla riapertura per il nodo e di nuovo i successori.

Fermare il ramo e riprenderlo dal nodo rimandato è esattamente la semantica del
`wait` già in casa, ed è ciò che rende il rinvio componibile con tutto il resto
del grafo senza casi speciali.

### 3. Si accoda un **puntatore**, non il messaggio già scritto

La riga in coda contiene *(automazione, soggetto, nodi da cui riprendere,
ancora d'episodio)*. Non contiene il testo.

Congelare il testo alle 03:00 e spedirlo alle 09:00 sarebbe una copia della
lavagnetta che invecchia: se nel frattempo il merchant corregge il nodo, parte
la versione vecchia — e ADR 0014 dice che il contenuto viene **solo** dalla
lavagnetta, non da una copia. Peggio per `ai_reply`: generare la risposta di
notte significa spendere una chiamata al modello che forse non verrà mai
spedita, e consegnare al cliente un testo che ignora tutto ciò che è successo
nel frattempo.

Rimandare il puntatore invece del payload fa cadere gratis tre cose giuste alla
ripresa: il testo è quello attuale del nodo, la finestra 24h viene rivalutata
(testo libero o template, secondo lo stato di *adesso*), e la guardia
d'episodio di ADR 0015 riparte — se il lead ha risposto nel frattempo, la
cadenza si spegne da sola invece di insistere.

Quello che **non** viene rivalutato sono le condizioni a monte: si riprende da
metà grafo, quindi un `condition_group` "solo se lead caldo" deciso alle 22:00
vale ancora alle 09:00. È la stessa semantica del resume di un `wait` — un nodo
già attraversato non si riattraversa — e va saputa: chi vuole una condizione
valutata al momento della consegna la mette **dopo** il nodo di invio, non
prima.

### 4. Una tabella e uno sweep, non un job arq differito

`automation_hours_queue` + il cron `flush_automation_hours_queue` ogni 5
minuti, cioè lo stesso impianto di `resume_after_hours`.

Le tre obiezioni di ADR 0028 §2 valgono qui quasi identiche:

* l'attesa va da una notte a un fine settimana lungo, e in Redis vivrebbe solo
  finché l'istanza non si riavvia;
* **il momento dell'apertura cambia**: il merchant che alle 08:00 si accorge di
  aver sbagliato gli orari e li corregge deve vedere la coda partire alle
  09:00, non all'orario che avevamo calcolato alle 03:00;
* gli id job stabili sono la trappola nota di arq.

Va detto per onestà che il motore accoda **già** i `wait` come continuazioni
arq in Redis, quindi il rischio (a) è già accettato altrove. La differenza che
giustifica il trattamento diverso: un `wait` è un'attesa che il merchant ha
disegnato, e perderla perde un tocco pianificato; questa è la sospensione
involontaria di un messaggio **già dovuto**. E l'obiezione (b) — gli orari
cambiano — non tocca il `wait` in nessun modo e tocca questa in pieno.

In più una tabella è ispezionabile: "quanti messaggi ha in coda questo
merchant" è una domanda che ci verrà fatta, e a un job differito in Redis non
si può fare.

### 5. Il claim vive nella riga, e la coda è idempotente

`claimed_at` con scadenza (15 minuti) come compare-and-swap: due passate dello
sweep si sovrappongono senza spedire due volte, e il claim si libera da solo se
il worker muore a metà.

L'unicità è su `dedup_key` = `offhours:{automazione}:{soggetto}:{nodi}` —
deliberatamente **senza** la dedup del run che l'ha prodotta, così due eventi
notturni sullo stesso nodo e sullo stesso lead producono un messaggio solo alla
riapertura (la regola di §4 di ADR 0028, applicata all'altro verso).

Il conflitto però **aggiorna `episode_anchor`**, non si limita a ignorare la
seconda scrittura. Tenendo la prima ancora si perdeva un invio legittimo in
silenzio: lead muto alle 02:00 → episodio in coda con ancora 02:00; il lead
risponde alle 04:00 (quell'episodio è chiuso, giustamente); torna muto e alle
06:00 ne parte uno nuovo, che collide e non aggiorna nulla; alle 09:00 la
guardia d'episodio confronta l'inbound delle 04:00 con l'ancora delle 02:00,
conclude "episodio finito" e non manda niente. L'ancora più recente viene da
un'autorizzazione più recente ed è quella contro cui va fatto il confronto.

Il ritorno distingue inserimento da aggiornamento (`xmax = 0`) perché l'evento
`automation.send_queued` deve essere emesso una volta sola.

### 6. Il promemoria appuntamento si rimanda, ma **mai oltre l'appuntamento**

`appointment_reminder` è il secondo interprete del grafo (ADR 0011) e non passa
da `_walk`, quindi ha bisogno del suo gate. Ma è anche l'unico invio con una
scadenza propria: rimandare alle 09:00 il promemoria di un appuntamento delle
08:30 consegna un avviso per un appuntamento già iniziato — un danno che oggi
non esiste e che il rinvio introdurrebbe.

Quindi: fuori orario non si invia e **non si consuma** la voce di schedule, così
il tick successivo (ogni 30 minuti) riprova — la coda, qui, è già implicita nel
ritentativo, e non serve una riga in tabella. Se però la riapertura cade **dopo**
`start_at`, il promemoria viene lasciato cadere e la voce consumata, con un
evento a registrarlo: meglio niente che un promemoria per ieri.

### 7. Nessun messaggio di cortesia sul percorso proattivo

Fuori orario il turno in ingresso manda `off_hours_message` perché il cliente ha
appena scritto e merita una risposta. Un'automazione rimandata non ha nessuno da
avvisare: il cliente non sa che stava per ricevere qualcosa. Mandargli "ti
scrivo domani" significherebbe inventare un testo che non è sulla lavagnetta,
cioè violare ADR 0014 per notificare un non-evento.

### 8. E di default è **acceso** — qui ADR 0028 §5 viene ribaltata

0028 lo teneva spento per non "cambiare in silenzio il comportamento di flussi
già in produzione". Guardando chi tocca davvero, l'argomento non regge:
`schedule.mode` vale `always` di default, quindi questa chiave **non ha alcun
effetto** finché il merchant non ha deliberatamente scelto `business_hours` o
`custom`. La popolazione interessata è esattamente quella che ha scritto
"rispondi solo 09:00-18:00" — e non intendeva "tranne i promemoria automatici
delle 3 di notte".

Tenerlo spento voleva dire chiedere allo stesso merchant di configurare due
volte lo stesso fatto del mondo, e nel frattempo svegliargli i clienti: è
l'errore che 0028 stessa (§1, sul riuso di `business_hours`) aveva rifiutato di
fare per il bot.

Resta una chiave della cascata: spegnibile per merchant, impostabile e lockabile
dal template d'agenzia. Chi vuole il vecchio comportamento lo ha con un click,
ed è una scelta che ora deve dichiarare invece di ereditare.

## Conseguenze

* Nuova tabella `automation_hours_queue` (migrazione 0051) con la RLS
  merchant-scoped delle altre `automation_*`.
* Nuovo cron ogni 5 minuti; `resolve_response_hours` viene chiamata una volta
  per run **solo** se il flusso ha almeno un nodo customer-facing (la cascata
  ha già la cache Redis a ~60s).
* Due nuovi `event_type`: `automation.send_queued` e `automation.send_dropped`.
* Un tetto d'età (14 giorni, come `resume_after_hours`) difende dall'agenda
  configurata male: senza, una riga con un merchant che non riapre mai
  resterebbe candidata per sempre.

## Sharp edge conosciuto, non risolto qui

La condizione di nodo `time_of_day` — quella che ADR 0028 §5 indicava come
alternativa per-flusso — valuta `minutes_of_day` in **UTC**
(`engine.py:_utc_minutes_of_day`), non nel fuso del merchant: oggi una finestra
"09:00-18:00" disegnata sul canvas è sfasata di un'ora o due rispetto all'ora
italiana. Il gate di questa ADR usa invece `ResponseHours`, che il fuso lo
rispetta (`schedule.timezone`). Sistemare `time_of_day` è un lavoro a sé, con
la sua migrazione di semantica per i flussi già disegnati.
