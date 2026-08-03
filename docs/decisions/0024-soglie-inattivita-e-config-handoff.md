# ADR 0024 — Una sola nozione di "inattività", e l'handoff configurabile dal merchant

Data: 2026-07-31
Stato: accettato

## Contesto

Il trigger "nessuna risposta" della lavagnetta non partiva. La causa non era nel
trigger: erano **due sweep indipendenti che definivano entrambi "silenzio"** con
soglie decise separatamente e nessuna nozione di quale dovesse venire prima.

* `followup_no_answer` (ogni 15 minuti) emette `lead.no_answer` per le
  conversazioni ferme oltre il `delay_minutes` del nodo trigger, e guarda **solo
  le conversazioni `active`**.
* `close_idle_conversations` (ogni ora al minuto :20) chiude quelle ferme oltre
  `conversation.idle_close_minutes`, il cui default è **120 minuti**.

Il default del campo "Ritardo 1° follow-up" nell'editor è anch'esso **120**. Le
due soglie coincidevano, quindi:

* con ritardo **> 120** la conversazione era già `closed` quando il follow-up
  sarebbe diventato eleggibile → **non partiva mai**, in modo deterministico;
* con ritardo **= 120** era una corsa fra i due cron: il tick di chiusura delle
  :20 precede quello di follow-up delle :30 per le conversazioni che maturano fra
  le :15 e le :20, cioè ~8% dei casi persi in silenzio.

Il docstring di `close_conversations.py` *affermava* l'invariante giusta ("la
soglia sta ben oltre la finestra di follow-up, 2° reminder a 1440 min") ma
nessuno la faceva rispettare, e il numero citato non corrispondeva più a nulla:
il secondo reminder a 1440 minuti apparteneva al design pre-ADR-0015, quando la
cadenza era guidata dallo scheduler e non dal grafo.

I test UC-03 non l'hanno intercettato perché stubbano `_scan_candidates`: saltano
esattamente il livello SQL in cui vivevano sia il filtro sullo stato sia il
pavimento di 30 minuti.

Un pavimento nascosto: `list_reminder_candidates` aveva `min_idle_minutes = 30`
come costante, che sovrascriveva silenziosamente qualunque `delay_minutes` più
corto configurato sul nodo. Il campo nell'editor accettava valori che il backend
ignorava.

Terzo problema, indipendente ma della stessa famiglia: **tre superfici di
configurazione per un comportamento, due inerti**. Le chiavi `no_answer.*`
(`first_reminder_min`, `second_reminder_min`, `max_followups` e i due testi) non
erano lette da nessuna riga di codice dopo ADR 0014/0015, ma restavano esposte e
bloccabili nel pannello dei template d'agenzia. Nel frattempo i due parametri che
governano davvero l'handoff non erano configurabili affatto: la SLA viveva in
`settings.handoff_sla_minutes` (una variabile d'ambiente **globale**, stessa per
ogni merchant della piattaforma) e la pausa del bot dopo una risposta scritta dal
telefono era una costante nel codice.

## Decisione

**1. L'ordinamento fra i due sweep è esplicito e deriva dallo stesso dato.**
Entrambi leggono i ritardi configurati sulla lavagnetta da un unico punto,
`AutomationRepository.enabled_trigger_delays(trigger_type, default_minutes)`:

* l'emettitore ne prende il **minimo globale** come pavimento di scansione;
* lo sweep di chiusura ne prende il **massimo per merchant**, più un margine
  (`_FOLLOWUP_GRACE_MINUTES = 30`, due tick dell'emettitore), come soglia minima
  di inattività: una conversazione con un follow-up ancora in arrivo non si
  chiude.

Il massimo e non il minimo, benché oggi l'emissione avvenga al minimo: tenere
aperta la conversazione più a lungo del necessario non costa nulla, chiuderla
troppo presto cancella un invio.

**2. Il pavimento di scansione non è più una costante.** È il ritardo più corto
davvero configurato sulla piattaforma (con un limite inferiore tecnico di 5
minuti, sotto il quale un cron che gira ogni 15 non può comunque reagire). Se
nessun merchant ha un'automazione `no_answer` attiva, la scansione di
`conversations` **non viene eseguita affatto**.

**3. Le chiavi `no_answer.*` sono rimosse** dallo schema, dai default di sistema
e dal pannello dei template. La cadenza dei follow-up vive interamente sul grafo.

**4. I due parametri dell'handoff entrano nella cascata di configurazione**, come
`escalation.sla_minutes` (default 15, 1–1440) e
`escalation.phone_echo_pause_minutes` (default 120, 5–10080), esposti nel
pannello merchant e nei template d'agenzia. `settings.handoff_sla_minutes` è
rimossa.

## Conseguenze

* Qualunque `delay_minutes` funziona, incluso oltre le due ore e sotto la
  mezz'ora.
* `handoff_sla_sweep` scandisce con il minimo consentito dallo schema e rifiltra
  ogni candidato con la soglia risolta del suo merchant. Costa una `resolve()` in
  più per candidato, ma solo per quelli che hanno davvero un'automazione in
  ascolto, e gli handoff aperti sono pochi per definizione.
* La migrazione `0048_drop_no_answer_config_keys` ripulisce i bag già salvati
  (`bot_configs.overrides`, `bot_templates.defaults`,
  `conversation_profiles.overrides` e `bot_templates.locked_keys`). Senza,
  `BotConfigSchema` con `extra="forbid"` — applicato **anche in lettura** su
  `GET /bot-config/{id}/resolved` — restituirebbe 500 a chi avesse toccato quei
  campi.
* La cache Redis del bag risolto avrebbe avuto lo stesso problema della
  migrazione: una voce scritta prima del deploy contiene ancora `no_answer.*` e
  `extra="forbid"` la rifiuterebbe finché non scade. Invece di documentare una
  finestra di errori, il suffisso della chiave è ora **versionato**
  (`__resolved__` → `__resolved_v2__`): i bag vecchi diventano irraggiungibili e
  scadono da soli. Va alzato a ogni rimozione futura di una `ConfigKey`.
* Chi aveva impostato la variabile d'ambiente `HANDOFF_SLA_MINUTES` su Railway la
  vedrà ignorata: il valore va reimpostato per merchant dal pannello (o come
  default nel template d'agenzia). Il default resta 15, quindi senza intervento
  il comportamento non cambia.

## Alternative scartate

* **Togliere il filtro `status == 'active'` dall'emettitore.** L'ancora di
  episodio garantisce già un'emissione sola, quindi non produrrebbe spam. Ma
  farebbe inviare follow-up su conversazioni dichiarate chiuse — e "chiusa" è
  anche la fine dell'episodio ai fini dei profili (ADR 0022) e l'innesco
  dell'estrazione obiezioni (UC-13). Avrebbe scambiato un bug visibile con una
  incoerenza di stato.
* **Un job differito per conversazione, riprogrammato a ogni inbound**, al posto
  dello sweep. È la soluzione più pulita — timing esatto, niente pavimento,
  niente ancora, niente cap a 500 — e la macchina esiste già (`_defer_by` per i
  nodi `wait`). Ma è una migrazione vera del modello di esecuzione, e non va
  mescolata con la correzione di un bug in produzione.
