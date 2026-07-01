# ADR 0014 — Automazioni: tutto dalla lavagnetta, niente hardcoded

Data: 2026-07-01 · Stato: accettato · Contesto: mentre un merchant stava configurando il promemoria appuntamento sulla lavagnetta, è partito un messaggio con un testo che lui non aveva scritto («Promemoria: hai un appuntamento 01/07 alle 11:30. A presto!»).

## Contesto

[[0011-automazioni-timing-dal-grafo]] aveva reso timing e contenuto delle 3 automazioni di
sistema (`no_answer`, `reactivation`, `booking_reminder`) configurabili dalla lavagnetta, MA gli
scheduler conservavano una **copia di fallback hardcoded** in Python, usata quando il nodo `send`
non aveva ancora un `free_text`:

- `appointment_reminder.py`: `fallback_text = f"Promemoria: hai un appuntamento {when}. A presto!"`
- `no_answer.py`: `REMINDER_TEXTS = {1: "Ciao! Eri ancora interessato?…", 2: …}`
- `reactivation.py`: `REACTIVATION_TEXTS = {1: …, 2: …, 3: …}`
- più gli override da ConfigKey (`NO_ANSWER_*_TEXT`, `REACTIVATION_MESSAGE`).

In più i flussi di sistema erano **seminati e attivi di default** (`ensure_system_automations` +
migrazione `0043`), e gli scheduler avevano un fallback «nessun flusso → invia coi default».

Effetto: un'automazione poteva inviare un messaggio con un testo **mai scritto dal merchant** — o
perché il flusso era attivo di default, o perché il nodo `send` era ancora vuoto mentre lo stava
configurando. Richiesta dell'owner, netta: **tutto ciò che riguarda le automazioni si configura
dalla lavagnetta. Niente hardcoded.**

## Decisione

**Il contenuto di un messaggio proattivo proviene ESCLUSIVAMENTE dalla lavagnetta.** Se non è
configurato, non si invia nulla.

1. **`decide_outbound` non ha più `fallback_text`.** Il testo viene solo da `step.free_text` (il nodo
   `send` sulla lavagnetta) oppure — per un nodo `ai_reply` — dal testo generato dall'AI a runtime
   (che viene comunque riversato in `step.free_text`). Se il nodo `send` non ha né `free_text` né un
   template approvato → **SKIP `no_content`**. Nessuna copia inventata.
2. **Rimossi tutti i testi hardcoded** dagli scheduler: `REMINDER_TEXTS`, `REACTIVATION_TEXTS`, la
   stringa "Promemoria…" e l'uso dei ConfigKey di testo (`NO_ANSWER_*_TEXT`, `REACTIVATION_MESSAGE`).
   Gli scheduler passano solo `step` + `context` (per il rendering di `{name}`/`{{appointment.datetime}}`).
3. **Nessun invio senza un flusso abilitato.** `decide_outbound(step is None) → SKIP `no_flow`; flusso
   disabilitato → SKIP `flow_disabled`. È il chokepoint unico di tutti gli invii proattivi (i 3
   scheduler + il motore dei flussi custom, che costruisce sempre un `flow_enabled=True`).
4. **Off di default.** `ensure_system_automations` semina i flussi con `enabled=False` (rimosso
   l'accensione automatica di ADR 0011). Il grafo di default resta seminato come **scheletro
   editabile** sulla lavagnetta (il merchant apre il flusso, scrive il testo, imposta i tempi,
   attiva). Il nodo `send` di default è già **vuoto** (`_DEFAULT_SEND.free_text = None`).

**Nessuna migrazione dati.** I flussi già auto-armati (attivi dal seeding vecchio / `0043`) NON
vengono toccati: con la regola sul contenuto un flusso attivo-ma-vuoto non invia più nulla (skip
`no_content`), quindi è già innocuo, e non si rischia di disabilitare un flusso che il merchant sta
configurando in quel momento. `enabled=False` vale solo per i merchant nuovi (seeding). Se in futuro
si vuole spegnere anche i flussi auto-armati esistenti, basta una migrazione mirata dedicata.

## Conseguenze

- Un messaggio proattivo parte **solo** se: (a) esiste un flusso abilitato sulla lavagnetta, **e**
  (b) il suo nodo `send` ha un testo o un template configurato lì. Altrimenti: skip silenzioso.
- Mentre il merchant configura un flusso (testo non ancora scritto) **non parte nulla**, anche se il
  flusso è attivo. Fine del problema originale.
- I `send`/`ai_reply` dei flussi **custom** seguono la stessa regola: nodo `send` vuoto → skip
  `no_content` (prima poteva inviare stringa vuota). Coerente: niente contenuto implicito da nessuna
  parte.
- Lo scheletro di default (timing 120/90/24 nei nodi del grafo) resta come **valore iniziale
  editabile sul canvas** — è "sulla lavagnetta", non hardcoded a runtime. Se in futuro si vuole anche
  quello vuoto, è una modifica banale a `_DEFAULT_SYSTEM_GRAPH`.
- I ConfigKey di testo (`NO_ANSWER_*_TEXT`, `REACTIVATION_MESSAGE`) restano definiti in schema ma
  **non più letti** (dead config; cleanup futuro).
- **Inverte** la parte «attivi di default» + «fallback hardcoded» di
  [[0011-automazioni-timing-dal-grafo]]; il resto di 0011 (timing dal grafo) resta valido. La
  migrazione `0043_enable_system_flows` (che accendeva i flussi) è di fatto neutralizzata: i flussi
  restano ma inerti finché il merchant non scrive il testo sulla lavagnetta.
