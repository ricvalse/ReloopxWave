# ADR 0026 — Quick win sull'agente portati da Amalia: contenimento dell'output del modello, stato reale dei messaggi, stile WhatsApp

Data: 2026-08-05
Stato: accettato

## Contesto

Il confronto `docs/confronto-agente-ai-amalia-vs-reloop-2026-08-05.md` ha
elencato 45 differenze verificate fra il motore agente di Amalia e il nostro. Di
queste, otto erano correzioni a costo quasi nullo. Le abbiamo applicate insieme
perché tre di esse si reggono a vicenda: due riguardano cosa succede **quando il
modello produce qualcosa di inutilizzabile**, e la terza è il motivo per cui quel
caso oggi è raro (e per cui deve restare tale).

Il difetto capofila: `_parse_structured` chiudeva il ramo di errore con
`reply_text=raw`. Un JSON che non rispetta lo schema — un `kind` inventato basta,
perché Pydantic valida la lista di azioni elemento per elemento e solleva sul
modello radice — diventava **il messaggio inviato al cliente su WhatsApp**. Non
solo: quel blob veniva persistito in `messages.content`, quindi finiva in inbox,
rientrava nella history del turno successivo e veniva raccolto nel dataset di
fine-tuning. Nessun sanitizer, nessun cap di lunghezza, nessun controllo
"sembra JSON" esisteva a valle. Il fail-safe di `_generate_and_deliver` non
copriva il caso: intercetta *eccezioni*, e `_parse_structured` è nato per non
sollevarne mai.

## Decisione

### 1. L'output del modello viene contenuto, non incollato

`_parse_structured` degrada a gradini invece che in uno:

1. parse conforme allo schema → invariato;
2. payload dentro un fence markdown → **ri-validato** una seconda volta, perché
   un fence può contenere JSON perfettamente conforme e scartarlo costerebbe un
   `book_slot` legittimo;
3. JSON valido ma fuori schema → si recupera `reply_text` e si tengono le azioni
   che validano **una per una** (`_salvage_actions`): Pydantic aborta la lista al
   primo elemento non valido, quindi un `kind` inventato accanto a un `book_slot`
   valido faceva sparire la prenotazione — in silenzio, perché il `reply_text`
   superstite è proprio la frase di attesa che lo schema insegna a scrivere;
4. scalare JSON nudo (`"Ciao Marco"`, `450`) → è prosa che per caso parsa, si
   tiene il testo;
5. output a forma di JSON ma irrecuperabile (anche **troncato**, che `json.loads`
   non parsa affatto) → `reply_text=""`.

"A forma di JSON" significa `startswith("{")` e **non** "il parse riesce": un
JSON tagliato a metà non è parsabile, e trattarlo come prosa avrebbe fatto uscire
il blob come prima. La parentesi quadra è esclusa di proposito — il prompt stesso
insegna al modello a scrivere note fra quadre (`[Il cliente ha inviato
un'immagine]`), quindi una prosa che inizia con `[` è plausibile. La prosa vera
resta intoccata: è una forma legittima, il fallback Anthropic non riceve mai
`response_format`.

Il `reply_text` vuoto è poi intercettato in `conversation_service`, che **ritenta
il turno una volta** (un output inservibile è quasi sempre un artefatto di
sampling; stessa forma del retry già presente per il coherence guard) e solo poi
manda la frase di cortesia con `escalate_human`. Lo stesso gate copre
`content=None` restituito da OpenAI su content filter o budget esaurito — un caso
che prima non era gestito da nessuna parte.

Il gate **non** imposta `llm_failed`. Quel flag significa "la chiamata LLM è
esplosa" e a valle scavalca due impostazioni del merchant: bypassa
`escalation.enabled = False` e `silent_handoff`. Riusarlo qui avrebbe permesso a
un singolo turno malformato di chiamare `claim_handoff`, che spegne `auto_reply`
in modo permanente — su un'agenzia che ha disattivato l'escalation proprio perché
nessuno presidia la inbox, il thread sarebbe ammutolito per sempre. Emettendo
l'azione in modo normale, sono quelle impostazioni a decidere.

### 2. Nessun cap di token sul percorso conversazionale

L'audit proponeva parametri di generazione espliciti "alla Amalia" (temp 0.4,
500 token). **Il cap non è stato adottato, deliberatamente.** Sul modello di
default (`gpt-5-mini`, un reasoning model) `max_tokens` diventa
`max_completion_tokens`, che è il budget di *reasoning + output visibile*: un
tetto dimensionato su una risposta WhatsApp viene mangiato dal reasoning e
restituisce JSON troncato o `content=None`. Cioè: il fix 3 avrebbe *causato* il
difetto che il fix 1 esiste per contenere. Se un cap servirà, dovrà essere
generoso (≥1500) e landare da solo, sotto osservazione.

La `temperature` è invece dichiarata al call-site (`_TURN_TEMPERATURE = 0.3`,
identica al default storico del client, quindi nessun cambio di comportamento):
sui gpt-5 il client la scarta comunque, ma raggiunge i modelli fine-tuned e il
fallback.

### 3. Una riga di messaggio non dichiara più un invio che non è avvenuto

`persist_assistant_message` nasce `status="pending"` — la riga viene scritta
*prima* dell'invio, quindi non poteva nascere `sent` (il default della colonna).
Il loop di consegna è avvolto in try/except e **l'eccezione viene rilanciata**,
così le semantiche di retry del job restano identiche a prima. A invio completato
la riga passa a `sent`; la promozione non è più condizionata alla presenza di un
`wa_message_id`, altrimenti un provider che non lo restituisce lascerebbe la riga
in `pending` per sempre.

Sul fallimento lo stato dipende da **quale** bolla è saltata. Il multi-bolla è
attivo di default (`delivery.multi_bubble_max = 2`) e la riga di messaggio è una
sola per l'intera risposta: se salta la bolla 2 il cliente ha già letto la prima,
quindi marcarla `failed` sarebbe sbagliato quanto il vecchio `sent`
incondizionato. Solo un fallimento sulla **prima** bolla significa che al cliente
non è arrivato nulla → `failed`; oltre, la riga resta `sent` e porta
`error.code = "partial_send"` con l'indice della bolla.

Nota su cosa **non** è stato risolto: al retry di arq il turno viene rigenerato e
reinviato (l'idempotenza sull'inbound protegge solo l'INSERT del messaggio in
entrata, non la produzione della risposta). È un comportamento preesistente, non
introdotto qui; ora però ogni tentativo lascia un record onesto invece di due
righe che dichiarano entrambe `sent`.

### 4. Gli strumenti non vengono annunciati se nessuno li eseguirà

`render_schema_hint(..., tools_available=False)` toglie le read-tool dall'enum,
il paragrafo STRUMENTI DI LETTURA e la frase finale della nota booking che le
cita. `run()` lo calcola da `tool_executor is not None and max_iterations > 1`,
cioè dalla condizione reale di esecuzione del loop; `run_proactive` passa sempre
`False`, perché lì un loop non esiste e le read-tool sono scartate per
costruzione.

Motivo: senza loop, annunciarle è un **vicolo cieco deterministico**. Il modello
emette `check_availability`, scrive la frase d'attesa che il paragrafo gli chiede
("un attimo che verifico"), la richiesta viene strippata prima del dispatcher e
la risposta definitiva non arriva mai. Il default resta `True` e
`render_schema_hint(None)` è **byte-identico** alla versione precedente
(verificato ricostruendo la funzione da HEAD e confrontando le stringhe — il
"golden test" storico è auto-referenziale e non lo avrebbe mai colto).

Conseguenza voluta: il **playground** — che chiama `run()` senza executor — non
promette più strumenti che non partiranno. Il suo prompt diverge quindi da quello
di produzione, il che è una perdita di fedeltà rispetto all'ADR 0009; la
consideriamo comunque un miglioramento netto rispetto a un vicolo cieco
permanente. La soluzione giusta resta quella di Amalia — eseguire le read-tool
davvero anche in dry-run, sono read-only per costruzione — ed è tracciata come
lavoro successivo, non fatta qui perché introdurrebbe chiamate GHL sincrone
dentro una richiesta HTTP.

### 5. Stile WhatsApp e lock di identità, in coda al prompt

Un blocco fisso (`_WHATSAPP_STYLE_BLOCK`) più un lock anti-drift
(`_style_lock_block`) chiudono il system prompt, **dopo** le direttive del
playbook. Il lock è il fix di un problema reale: un thread mescola turni del bot
e risposte scritte a mano dall'operatore dall'inbox, e il modello legge quel
registro umano come stile di casa. Il nome dell'assistente arriva dalla nuova
chiave di cascata `bot.assistant_name` (default `None` → non si dichiara alcun
nome).

Sul rapporto con l'**ADR 0018**, che vuole le direttive del playbook per ultime
per salienza: il blocco è dichiaratamente *sulla forma* ("riguarda la FORMA del
messaggio; su contenuto e comportamento restano prioritarie le regole qui
sopra"), quindi la precedenza delle direttive sul comportamento non è toccata. La
posizione finale serve al lock, che deve stare il più vicino possibile alla
history che ha il compito di scavalcare.

Cosa **non** è stato portato da Amalia, o è stato corretto rispetto alla prima
stesura di questo blocco (tutti e quattro i punti sono difetti trovati in
revisione, non scelte di stile):

* «rispondi nella lingua del cliente» contraddice la REGOLA ASSOLUTA DI LINGUA
  che già iniettiamo (vince la lingua configurata, per scelta);
* «rispondi SOLO con il messaggio, nient'altro» contraddice l'involucro JSON che
  lo schema impone: la regola è ancorata al campo — «in `reply_text` metti SOLO
  il testo da mandare al cliente»;
* «WhatsApp non interpreta il markdown» è **falso** — rende `*grassetto*`,
  `_corsivo_` e i blocchi di codice, come documenta il linter template del
  prodotto stesso. Una motivazione sbagliata in un prompt è peggio di nessuna
  motivazione, perché il modello ci generalizza sopra: restano vietate solo le
  sintassi davvero non supportate (titoli `#`, link `[testo](url)`, grassetto a
  doppio asterisco). Gli elenchi restano ammessi, perché lo splitter tiene
  deliberatamente insieme una lista e la sua riga di introduzione (`_holds_list`);
* «messaggi brevi» come regola fissa annullava in silenzio
  `bot.verbosity = dettagliato`, quindi è stata tolta.

Sul percorso proattivo il blocco perde la riga «il cliente può aver inviato più
messaggi di fila»: lì non è arrivato nulla, e chiederlo contraddice la direttiva.

### 6. Contorno

* `button` e `location` nel parser webhook. Il primo era un **drop silenzioso**:
  la risposta a un template con quick-reply non ha placeholder, quindi il router
  scartava l'evento e il cliente che premeva "Sì, confermo" non veniva sentito.
  `interactive` era già gestito; il ramo degli echo Coexistence non è toccato,
  perché i bottoni li preme il cliente, non il merchant.
* Log `tool.requested`/`tool.executed` in `read_tools.py` (il logger era
  dichiarato e mai usato: l'intera feature di grounding era invisibile in
  produzione) e `orchestrator.tool_call_dropped` quando una read-tool non verrà
  mai eseguita. Si logga solo la **forma** del payload (`payload_keys`), mai i
  valori: le chiavi documentate sono innocue, ma `payload` è un
  `dict[str, Any]` non validato riempito dal modello e `json_object` non impone
  lo schema, quindi può comparire una chiave che riecheggia le parole del
  cliente — e structlog finisce su stdout *e* nei breadcrumb Sentry, nessuno dei
  due dentro il perimetro DSAR. Il `summary` non è loggato: nomina appuntamenti.
* `agent.max_tool_iterations`: il fallback di codice passa da `1` a `3`, per
  allinearsi a `SYSTEM_DEFAULTS`. Quel default scatta solo se il resolver
  solleva, e `1` significava tool-use acceso con vicolo cieco garantito.

## Conseguenze

* 821 unit test verdi (baseline 788, +33 nuovi). `render_schema_hint(None)`
  byte-identico a HEAD, verificato ricostruendo la funzione da HEAD e
  confrontando le stringhe: il "golden test" storico confronta la funzione con la
  costante che essa stessa produce a import-time, quindi è tautologico e non
  avrebbe rilevato nulla. ruff (58) e mypy (26) invariati rispetto alla baseline:
  gli errori preesistenti non sono stati toccati, CLAUDE.md vieta le
  riformattazioni collaterali.
* Nessuna migrazione: `messages.status` è già `String(16)` e documenta
  `pending | sent | delivered | read | failed`.
* `frontend/packages/api-client/src/generated.ts` rigenerato — due righe, la sola
  `assistant_name` — e il campo è stato aggiunto a `bot-config/sections.ts`:
  quella lista è dichiarativa, e senza la voce la chiave sarebbe stata
  irraggiungibile dal merchant, cioè codice morto. `assistant_name` è risolto su
  tutti e tre i percorsi (inbound, automazione proattiva, playground), altrimenti
  il bot cambierebbe nome fra un turno e l'altro nello stesso thread.
* Resta aperto: fedeltà del playground (punto 4); la duplicazione della risposta
  al retry del job (preesistente — l'idempotenza copre solo l'INSERT
  dell'inbound, non la generazione); il residuo di vicolo cieco quando il tool
  fallisce su tutte le iterazioni disponibili; e la guardia deterministica
  testo↔azione, che è il vero passo successivo e non è un quick win.
