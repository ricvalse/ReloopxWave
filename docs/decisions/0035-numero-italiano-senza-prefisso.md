# ADR 0035 — Repair automatico del prefisso "39" su numeri mobili italiani senza prefisso

Data: 2026-09-16
Stato: accettato

## Contesto

Merchant Ghilea, conversazione `393208043592` (lo stesso lead del repro
`test_automation_ghilea_repro.py` / ADR 0034): il lead esiste come **due**
righe `Lead`/`Conversation` distinte. Una è quella toccata dall'automazione
"Nuovo lead" (trigger `crm_opportunity_created`, creata da GHL), l'altra è
quella su cui il lead scrive davvero ("Si certo") dopo aver ricevuto il
template. Il messaggio dell'automazione parte su una riga, la risposta arriva
sull'altra: dal punto di vista del merchant il bot "non risponde mai" — non
perché l'AI ignori il lead, ma perché sta guardando due conversazioni diverse
che pensa siano la stessa persona.

Causa, verificata riga per riga (workflow di verifica parallela, 3 ipotesi
indagate in `workers/conversation/handlers.py` e
`libs/ai_core/src/ai_core/conversation_service.py`):

* Il percorso GHL (`_handle_crm_create`, handlers.py:452-611) risolve/crea
  `Lead`/`Conversation` con `phone = normalize_phone(_ghl_phone(payload))`
  (riga 484) — il numero così come l'agenzia l'ha digitato nel contatto GHL.
* Il percorso WhatsApp inbound (`handle_inbound_persist`,
  `generate_and_send_reply`, `handle_phone_app_echo`, tutti in
  `conversation_service.py`) usava `from_phone`/`customer_phone` **grezzo**,
  preso da `msg.get("from", "")` nel payload 360dialog — **zero** chiamate a
  `normalize_phone` in tutto il file (verificato via grep).
* `normalize_phone` (`libs/shared/src/shared/phone.py`) riduce a sole cifre e
  toglie un `00` iniziale, ma un numero **senza prefisso internazionale**
  ("333 123 4567", come capita spesso quando chi compila il contatto su GHL
  non aggiunge il "+39") passava invariato — per design: "no per-merchant
  country default in V1", commento esplicito nel codice da prima di questo
  ADR.

Risultato: un contatto GHL con telefono in formato nazionale produce
`wa_contact_phone = "3208043592"`; lo stesso lead che scrive su WhatsApp arriva
con `from = "393208043592"`. Due stringhe diverse → `ConversationRepository`
matcha su uguaglianza esatta di `wa_contact_phone` (nessuna query fa fuzzy
match) → due righe `Lead` (univoco per `(merchant_id, phone)`, quindi
l'upsert non collassa le due forme) e due righe `Conversation`.

## Decisione

### 1. `normalize_phone` ripara il caso "mobile italiano in formato nazionale"

`libs/shared/src/shared/phone.py`: dopo la normalizzazione a sole cifre, se il
risultato è **esattamente 10 cifre e inizia con "3"** (`_IT_MOBILE_NATIONAL`,
la forma di ogni cellulare italiano in formato nazionale: 3xx xxx xxxx),
antepone "39". La forma è scelta apposta per essere inequivocabile: un numero
che arriva già con prefisso internazionale è sempre 11+ cifre (o 10 cifre che
non iniziano per "3"), quindi questo ramo non può mai correggere un numero già
corretto. Non è un default-paese generico (non lo mettiamo per la Germania o
la Francia): è la riparazione della forma specifica che questo prodotto vede
davvero, dato che è un prodotto per il mercato italiano.

Resta *non* riparabile — come prima — un numero locale che non ha questa
forma (un fisso, un cellulare estero senza prefisso): lì non c'è modo di
indovinare il paese senza contesto, e il codice continua a passarlo invariato
a sole cifre, invece di inventare un prefisso sbagliato.

### 2. Il percorso WhatsApp inbound ora normalizza anche lui

`conversation_service.py`: `handle_inbound_persist`, `generate_and_send_reply`
e `handle_phone_app_echo` applicano `normalize_phone(x) or x` sul numero del
mittente prima di usarlo per risolvere/creare `Lead`/`Conversation`. In pratica
è quasi sempre un no-op (360dialog manda già sole cifre con prefisso), ma
rende i due percorsi **provabilmente** simmetrici invece di affidarsi
implicitamente al fatto che il formato del payload 360dialog non cambi mai —
e beneficia gratis di qualunque futuro miglioramento di `normalize_phone`.

## Conseguenze

* 1121 unit test verdi (era 1120 in ADR 0034; qui +2 nuovi test di
  `normalize_phone`, −1 test che asseriva il comportamento "non riparato" ora
  superato, per un netto di +1). ruff/ruff format e mypy invariati rispetto
  alla baseline (stesso set di errori pre-esistenti, verificato per diff prima/
  dopo su ognuno dei tre strumenti — CLAUDE.md vieta le riformattazioni
  collaterali, quindi non toccati).
* Nessuna migrazione: nessuna colonna nuova, solo una funzione pura e tre punti
  di chiamata.
* **Effetto SOLO sui nuovi contatti/conversazioni da questo momento in poi.**
  Non fatto, e fuori dallo scope di codice di questo ADR: **le righe duplicate
  già esistenti in produzione** (come le due conversazioni di Ghilea) non
  vengono unificate automaticamente — richiede una riconciliazione a mano (o
  uno script di backfill scritto ad hoc, mai da eseguire alla cieca su dati di
  produzione) che identifichi le coppie `Lead`/`Conversation` con lo stesso
  numero una volta ri-normalizzato e le fonda, spostando i messaggi sulla riga
  superstite. Non è stato fatto qui perché tocca dati di produzione e non è
  disponibile un accesso DB da questa sessione.
* Resta un'assunzione non testata in produzione su questo giro: che nessun
  merchant abbia legittimamente un lead con un numero locale di 10 cifre che
  inizia per "3" e **non** è un cellulare italiano (es. un prefisso estero che
  per coincidenza ha quella forma). Considerato rischio trascurabile dato il
  mercato del prodotto, ma va tenuto a mente se in futuro arriverà un merchant
  fuori Italia.
