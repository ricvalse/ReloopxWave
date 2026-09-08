# ADR 0031 — Il bot risponde solo dove è passata un'automazione

**Stato:** accettata — 2026-09-08
**Contesto:** UC-01, CC-CONFIG, lavagnetta automazioni
**Migrazione:** 0052 (`conversations.last_automation_at`)
**Si somma a:** ADR 0028/0030 (orari), ADR 0017 (handoff), UC-06 (opt-out) — è un
gate in più nella stessa catena, mai un `or` che ne scavalca uno.

## Il problema

`bot.auto_reply_enabled` è un interruttore a due posizioni: il bot risponde a
**tutti** o a **nessuno**. Per un merchant che usa WhatsApp anche come numero
pubblico questa è una scelta impossibile. Acceso, l'AI risponde al fornitore, al
curioso, al numero sbagliato. Spento, non risponde nemmeno a chi sta rispondendo
a una campagna che il merchant ha appena pagato — cioè proprio dove l'automazione
serviva.

Quello che serve è: *l'AI lavora le conversazioni che l'azienda ha aperto; le
altre le guarda una persona.*

## Le decisioni

### 1. Il permesso è un latch, non una finestra temporale

**«Se a questa conversazione è stata mandata almeno un'automazione, il bot
risponde. Altrimenti tace.»** Si arma al primo invio automatico e non si disarma.

La prima ipotesi era una finestra scorrevole ("automazione mandata nelle ultime N
ore"). È stata scartata: risolveva la *decadenza*, che nessuno aveva chiesto, al
prezzo di una terza soglia temporale da tarare — accanto a `no_answer.delay_minutes`
e ai `wait` del grafo — di due percorsi di valutazione, e di un caso limite
brutto: il cliente che risponde alla campagna dopo tre giorni non veniva più
seguito. Cioè esattamente il lead che il merchant ha pagato per acquisire.

Il latch è anche la regola che il merchant sa ripetere a memoria, il che conta
più della precisione: è lui a doverla spiegare ai suoi.

**Conseguenza accettata consapevolmente:** una conversazione nata da campagna e
poi diventata chiacchierata generica resta servita dal bot per sempre. Se un
giorno darà fastidio, la decadenza si aggiunge senza migrazione (§5).

### 2. Una colonna denormalizzata, non un predicato sui messaggi

`conversations.last_automation_at`. La stessa domanda sarebbe rispondibile con un
`EXISTS` su `messages.automation_id`, che c'è dalla 0047 — ma il gate ha la
conversazione **già caricata** in sessione, quindi leggere un suo campo costa
zero query, mentre l'`EXISTS` ne costerebbe una su ogni messaggio in ingresso di
ogni merchant in modalità ristretta.

La denormalizzazione qui è gratis perché **il punto di scrittura è già unico**
(§3). Non è il caso generale in cui denormalizzare significa tenere allineate due
verità.

**Timestamp e non booleano**, a parità di costo: risponde alla stessa domanda
(`IS NOT NULL`) ma dice anche *quando*, che è ciò che serve per la decadenza
futura, per il debug ("perché il bot ha risposto qui?") e per un eventuale filtro
in inbox. Con un booleano quel giorno servirebbe una seconda migrazione.

### 3. Il timbro sta in `send_and_persist_decision`, e questo non è un dettaglio

`workers/outbound.py` — il collo di bottiglia da cui passa **ogni** invio
proattivo del sistema. Il suo docstring lo dichiarava già per l'attribuzione
della 0047: *«è qui che passa ogni invio proattivo del sistema, quindi è l'unico
punto in cui la provenienza può essere registrata»*.

Timbrare lì, **incondizionatamente**, invece di guardare `automation_id`, ha tre
conseguenze che nessun'altra collocazione dà insieme:

- **Il promemoria appuntamento è coperto.** Non ha un'automazione dietro (è uno
  scheduler) e non passa `automation_id`. Appoggiandosi a quel campo, chi risponde
  «sì, confermo» a un promemoria **non riceverebbe risposta**: il caso d'uso
  numero uno di un bot di prenotazioni, rotto in silenzio.
- **Un futuro `broadcast` sarà coperto** senza che nessuno debba ricordarsene —
  purché passi di lì, che è già un invariante del progetto.
- **Non ci sono invii senza timbro.** `_resolve_context` ritorna `None` se manca
  `wa_phone_number_id`, che viene solo dalla conversazione: un run con contesto
  valido ha sempre `conversation_id`, quindi `_send_proactive` prende sempre il
  ramo che persiste. Il ramo senza `Message` è irraggiungibile.

Il timestamp viene dall'**orologio del database**, come in `mark_off_hours_pending`
e per la stessa ragione: dentro una transazione Postgres `now()` è costante, quindi
il timbro coincide col `created_at` del messaggio scritto nella stessa transazione
— ed è ciò che rende il valore scritto a runtime indistinguibile da quello
ricostruito dal backfill della 0052, che aggrega su `MAX(messages.created_at)`.

### 4. Il gate va **prima** degli orari, non dopo

Nella catena di `conversation_service.py` il nuovo asse sta subito dopo la
staleness e **prima** di `resolve_response_hours`.

Da `would_reply_but_for_hours` discendono il marcatore di ripresa
(`mark_off_hours_pending`) e il messaggio di cortesia fuori orario. Valutando lo
scope **dopo** gli orari, un contatto a freddo che scrive di notte riceverebbe
comunque «ti rispondiamo domani» e verrebbe messo in coda per la riapertura: il
bot parlerebbe proprio a chi il merchant ha escluso, e l'operatore troverebbe un
thread che sembra già preso in carico. In più, valutandolo prima si risparmia la
risoluzione degli orari quando non serve.

È l'unica decisione di questo ADR che un rifattore distratto può ribaltare senza
accorgersene, ed è per questo che ha un test suo
(`test_solo_automazioni_a_freddo_di_notte_non_manda_la_cortesia`).

### 5. La modalità governa l'inbound, **non** il grafo

Il motore automazioni non è stato toccato. Continua a guardare solo
`run_ctx.ai_paused` e a non consultare né `bot.auto_reply_enabled` né lo scope.

Applicare lo scope anche lì sarebbe un **deadlock**: nessuna automazione potrebbe
più agganciare una conversazione fredda, e la modalità si autodistruggerebbe al
primo giro. Ne segue che un'automazione con trigger `message_received` **parte
comunque**, anche a gate chiuso — `message.received` è emesso lo stesso — ed è la
valvola di sfogo voluta: chi vuole rispondere qualcosa al contatto freddo lo
disegna sulla lavagnetta, coerentemente con ADR 0014. E quell'invio arma il
latch, quindi dal secondo messaggio in poi la conversazione è servita dal
percorso inbound veloce.

Da dire nell'help e non da nascondere: latenza fino a ~60-70 s (cron
`automation_dispatch`), nessun debounce, cap di un `ai_reply` per job.

### 6. `bot.auto_reply_enabled` resta il master switch, invariato

`bot.auto_reply_scope` si **somma** (AND), non sostituisce. Un `bot.reply_mode`
che rimpiazzasse il booleano è stato scartato per una ragione non recuperabile:
**un'agenzia che ha lockato `bot.auto_reply_enabled` non ha lockato la chiave
nuova**, quindi il merchant aggirerebbe il lock durante la deprecazione.

Fail-open per costruzione: `_resolve_optional_str` degrada a `None` a ogni errore
e `None` vale `"tutti"`. Se Redis è giù il bot si comporta come oggi, invece di
ammutolire su tutti i merchant insieme.

### 7. Fuori perimetro: silenzio, non handoff

Il contatto a freddo viene persistito, il media scaricato, i segnali comportamentali
e lo score di intake aggiornati, l'evento `message.received` emesso con
`auto_reply_skipped=True, reason="no_automation"`. Ma **niente handoff automatico
e niente messaggio di cortesia**.

`claim_handoff` è un takeover **permanente** (non esiste un-handoff automatico):
userebbe ogni numero sbagliato per trasformare un thread in lavoro manuale per
sempre, e spegnerebbe la conversazione anche per la campagna di domani. Una
cortesia scritta in Python violerebbe ADR 0014.

**Costo onesto, dichiarato:** sentiment e scoring cumulativo non si aggiornano su
questi turni (vivono nel percorso di generazione della risposta), quindi i lead
mai toccati da un'automazione restano allo score di solo intake.

## Osservabilità

Il rischio dominante non è il falso positivo, è il **falso negativo silenzioso**:
`auto_reply_enabled=false` non sorprende nessuno, qui invece il merchant crede che
il bot stia lavorando. Perciò `reason="no_automation"` esce su tre canali —
l'evento analytics `message.received`, `PersistOutcome.reason`, e il log
`uc01.reply_suppressed_at_flush` — e non ricade mai nel generico
`conversation_off`, che in analytics è indistinguibile da un takeover operatore.
È lo stesso prezzo che il progetto ha già pagato una volta con `off_hours`.

## Conseguenze

- Default `"tutti"`: nessun merchant cambia comportamento al deploy. Opt-in per
  merchant, imponibile e lucchettabile dall'agenzia via template.
- Il backfill della 0052 ricostruisce lo storico dai messaggi già scritti, quindi
  chi accende la modalità trova le campagne in volo **già dentro** il perimetro.
  Senza, il bot ammutolirebbe su tutte le campagne in corso.
- Il gate vive in due punti (turno in ingresso e flush/ripresa) ma la logica in
  uno solo, `_automation_scope_blocks`: i due percorsi erano già divergenti prima
  di questa modifica, e un terzo asse copiato a mano avrebbe garantito la
  divergenza successiva.

## Limiti noti, non risolti qui

- **Non è sovrascrivibile per profilo di conversazione** (ADR 0022): gli helper
  `_resolve_*` non passano `profile_id`. È proprio il genere di interruttore che
  un profilo vorrebbe cambiare. Dichiarato, non scoperto dopo.
- **L'opt-out resta non applicato in uscita** (zero controlli in `outbound.py` /
  `engine.py`). Questa modalità non lo peggiora, ma lo rende più visibile: in
  `solo_automazioni` le uniche risposte automatiche sono quelle legate a campagne.
  Va corretto a parte.
- **Vincolo per chi aggiungerà `broadcast`:** deve passare da
  `send_and_persist_decision`. Un canale d'uscita che non timbra farebbe smettere
  questa modalità di coprire il suo caso d'uso principale, in silenzio.
