# ADR 0026 — Orari di risposta dell'assistente

**Stato:** accettata — 2026-08-05
**Contesto:** UC-01, CC-CONFIG
**Sostituisce:** il campo `schedule.active_hours` (stringa a fascia unica)

## Il problema

Il merchant deve poter dire *quando* l'assistente risponde: sempre, oppure
dentro certi orari. E fuori da quegli orari la domanda del cliente non deve
sparire: il bot deve rispondere alla riapertura — a meno che, nel frattempo,
non abbia già risposto un operatore.

Esisteva già uno scheletro, ma non faceva nessuna di queste tre cose:

* `schedule.active_hours` era una stringa (`"24/7"` o `"09:00-18:00"`) applicata
  **identica a tutti e sette i giorni**. Non sapeva esprimere né il sabato
  diverso dal lunedì né la pausa pranzo — cioè quasi ogni negozio italiano.
* Fuori orario il pipeline mandava `off_hours_message` **come risposta del
  turno** e chiudeva lì. La domanda del cliente non veniva risposta mai, né
  allora né dopo. Con più messaggi notturni, la cortesia partiva a ogni
  messaggio: tre domande, tre "ti risponderemo al più presto", zero risposte.
* Non esistendo nessuna ripresa, non esisteva nemmeno il controllo
  sull'operatore.

Il ramo, inoltre, era **privo di test**: è così che è potuto restare rotto senza
che nessuno se ne accorgesse.

## Le decisioni

### 1. Tre modalità, e gli orari di apertura si riusano

`schedule.mode` ∈ `always` | `business_hours` | `custom`.

`business_hours` **riusa la tabella `business_hours` esistente** — quella che il
booking già consulta e che il cron notturno sincronizza con il calendario GHL,
pause pranzo e chiusure straordinarie comprese. L'alternativa (una settimana-tipo
tutta nuova per il bot) avrebbe creato due posti da aggiornare per lo stesso
fatto del mondo, con la garanzia che prima o poi divergono.

`custom` esiste perché i due concetti **non coincidono sempre**: un negozio
aperto 09:00-13:00 / 15:00-19:00 può volere che l'assistente risponda 08:00-22:00.

Vincolo ereditato da dichiarare: `business_hours` ha un `CHECK
open_time < close_time`, quindi in quella modalità **una fascia notturna è
inesprimibile**. Chi la vuole usa `custom`.

`always` resta il default e corto-circuita prima di toccare l'evaluator.

### 2. Ripresa con uno sweep, non con un job differito

Lo sweep `resume_after_hours` gira ogni 5 minuti e cerca le conversazioni
marcate il cui merchant è tornato dentro i propri orari.

L'alternativa naturale — accodare un job arq con `_defer_until=prossima
apertura` — è stata scartata per tre motivi, tutti già emersi in questo repo:

* l'attesa dura da una notte a un fine settimana lungo, e per tutto quel tempo
  la promessa vivrebbe **solo in Redis**: un riavvio e le risposte svaniscono;
* **il momento dell'apertura cambia** (il merchant modifica gli orari, aggiunge
  una chiusura): un job accodato porta con sé un orario deciso ieri;
* gli id job stabili sono una trappola nota — arq rifiuta un id il cui job *o
  risultato* esiste ancora, e ogni ripresa dopo la prima sparirebbe in silenzio.

Lo stato sta in una colonna (`conversations.off_hours_pending_at`, indice
parziale), come i tre emettitori edge-triggered già in casa. Sopravvive ai
riavvii, recupera i tick persi, rivaluta gli orari freschi a ogni passata.

Il costo accettato: la risposta arriva **entro** cinque minuti dall'apertura,
non all'istante. Su un'attesa già durata una notte è un arrotondamento.

### 3. "Ha già risposto un operatore" non si legge da `sender_type`

Il caso più comune è già coperto strutturalmente: rispondere dal composer
chiama `claim_manual_handoff`, che spegne `auto_reply` e apre un handoff — e lo
sweep esclude quelle righe in SQL.

Restava un buco: il merchant che risponde **dall'app WhatsApp del telefono**
(mirroring 360dialog). Quella strada non apre un handoff, mette solo
`ai_disabled_until = now + 2h` — che dopo una notte è scaduta. Senza un
controllo esplicito il bot avrebbe parlato sopra un operatore che aveva già
chiuso la questione all'una di notte.

`human_replied_since` copre quel buco, e **non si fida di `sender_type`**:
l'endpoint del composer costruiva il messaggio senza passare quel campo, che
prendeva quindi il default della colonna (`'ai'`) mettendo `'human'` solo dentro
`meta`. Tutto lo storico dei messaggi scritti a mano è indistinguibile dal bot,
se si guarda la sola colonna. Il discriminante robusto è `role='agent'`, che il
router ha sempre passato. Il writer è stato comunque corretto.

### 4. Cortesia una volta per episodio, non per messaggio

Il claim atomico del marcatore (`WHERE off_hours_pending_at IS NULL`) decide chi
parla: vince una sola scrittura, e solo il vincitore manda la cortesia.

### 5. Le automazioni restano fuori, salvo opt-in

`schedule.apply_to_automations`, default `False`.

Il timing degli invii proattivi viene **dalla lavagnetta** (ADR 0011/0014).
Accendere il gate di default cambierebbe in silenzio il comportamento di flussi
già in produzione, e per chi lo vuole per-flusso esiste già la condizione
`time_of_day`. Il requisito parla dell'*assistente che risponde*, cioè UC-01.

> **Nota:** la chiave è definita e risolta, ma il gate nel motore delle
> automazioni **non è ancora cablato**: accenderla oggi non ha effetto. È il
> primo pezzo di lavoro successivo, insieme alla scelta fra "rimanda" e "salta"
> (rimandare è preferibile — saltare perde il messaggio per sempre).

### 6. `active_hours` viene rimossa, non lasciata inerte

La migrazione 0049 converte i valori salvati in `mode` + `weekly` sulle tre
superfici di override e traduce i lock. Lasciarla come campo morto avrebbe
ripetuto l'errore delle `no_answer.*` — chiavi ancora esposte nel pannello che
nessuna riga di codice leggeva più (rimosse in 0048). Un orario che l'utente
imposta e che il bot ignora è peggio di un campo assente.

Conseguenza obbligata: `RESOLVED_CACHE_KEY` sale a `v3`. `BotConfigSchema` ha
`extra="forbid"` **anche in lettura**, quindi un bag cachato con la chiave morta
manderebbe in 500 il pannello finché non scade.

### 7. Nessun validator "serve almeno un giorno aperto"

Sarebbe una trappola: lo stesso modello valida il bag *parziale* in scrittura e
quello *risolto* in lettura, quindi un vincolo che incrocia due campi può essere
rispettato da ogni singolo salvataggio e violato dalla loro somma — 500 sul
pannello, proprio sulla pagina da cui si dovrebbe correggere l'errore. La difesa
sta a valle (fail-open + warning) e nella UI, prima del salvataggio.

## Conseguenze

* **Fail-open ovunque.** Qualunque errore di risoluzione produce "sempre
  aperto". Un bot che risponde di notte è un fastidio; un bot muto è un guasto
  invisibile finché non arriva il reclamo.
* **Interazione con la chiusura per inattività.** `close_idle_active` chiudeva
  qualunque conversazione ferma da 120 minuti: quella sospesa la sera sarebbe
  stata chiusa all'01:20, e lo sweep — che guarda solo le `active` — non
  l'avrebbe più trovata. Ora le conversazioni in attesa sono escluse. È la
  stessa classe di errore dell'ordinamento chiusura/follow-up.
* **La staleness non si applica alla ripresa.** `schedule.inbound_staleness_min`
  difende dal backlog accidentale (worker giù), non da un'attesa che abbiamo
  deciso noi e annunciato al cliente.

## Questioni aperte (prodotto)

1. **Chiusure oltre le 24 ore.** Venerdì 23:00 → lunedì 09:00 sono 58 ore: la
   finestra di servizio WhatsApp è chiusa e il testo libero è vietato. Oggi la
   ripresa viene abbandonata ed emette `conversation.resume_expired`, che una
   automazione può intercettare. Le alternative sono un template di
   ri-aggancio o un handoff automatico. **Serve una scelta**, altrimenti ogni
   ponte lungo produce clienti ignorati.
2. **`business_hours` come sorgente autoritativa.** In quella modalità,
   modificare gli orari dal pannello Prenotazioni cambia anche quando risponde
   il bot — in silenzio, e con push sincrono verso GHL. È il comportamento
   voluto o serve una conferma esplicita?
3. **La cortesia conta come "risposta"?** Viene persistita ed entra nella
   metrica «Risposte inviate» e nel tempo medio di risposta. Passando a una
   cortesia per episodio quei contatori cambiano rispetto allo storico.
