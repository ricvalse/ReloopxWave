# ADR 0026 — Handoff: istruzioni configurabili dalla UI, e un nome solo in tutta la piattaforma

Data: 2026-08-05
Stato: accettato
Supersede parzialmente: —
Correlati: 0017 (handoff exactly-once), 0020 (notifica Slack), 0023/0024 (gate proattivo, SLA per merchant), 0018 (playbook)

## Contesto

### 1. Le istruzioni di handoff erano tre parole in una costante

Tutto ciò che il modello sapeva su *quando* passare la conversazione a una
persona stava in una riga di `orchestrator._ACTION_SNIPPETS`:

> «quando l'utente è arrabbiato, minaccia reclami/azioni legali, o chiede
> esplicitamente una persona»

L'unica eccezione — «un media da solo non è un motivo di handoff» — era anch'essa
cablata, e vive nel codice perché un caso reale l'aveva richiesta. Questo è il
sintomo: **ogni edge case di un merchant diventava una modifica al codice, che poi
valeva per tutti i merchant.** Un settore in cui «reclamo» è un termine tecnico e
uno in cui è un segnale di rabbia non possono condividere gli stessi criteri.

Le chiavi `escalation.*` già esistenti non aiutavano, perché **nessuna di esse
entra nel prompt**: agiscono tutte a valle della generazione.

| chiave | cosa fa davvero |
|---|---|
| `escalation.enabled` | filtra l'azione **dopo** che il modello ha risposto |
| `escalation.critical_keywords` | **non provoca l'handoff**: sceglie il modello più capace |
| `escalation.handoff_message` | sostituisce la reply già generata |
| `escalation.silent_handoff` | sopprime l'invio |

Il caso peggiore era già annotato nel codice: con `enabled=false` il modello
scrive comunque «ti passo un operatore», la frase **parte davvero** verso il
cliente, e solo l'azione viene scartata. Il cliente resta ad aspettare una
persona che non arriverà.

### 2. Due nomi per la stessa cosa

Il DB parlava già di handoff (`conversations.handoff_at`, `handoff_reason`,
`handoff_resolved_at`, cron `handoff_sla_sweep`), il resto della piattaforma di
escalation (`escalation.*`, `escalate_human`, `EscalateHumanHandler`,
`escalation_predictor`). Chi legge il codice non capisce se siano due meccanismi
o uno.

### 3. Due difetti verificati durante l'analisi

- **La valvola di sicurezza non esisteva.** La docstring di `render_schema_hint`
  prometteva che `escalate_human` restasse disponibile anche fuori
  dall'allowlist del playbook. Il codice non lo faceva e nessun test lo copriva:
  verificato a runtime che con allowlist `{book_slot}` l'enum diventa
  `book_slot|none`. Un playbook che restringeva le azioni — per impedire
  prenotazioni indesiderate — **spegneva in silenzio l'unica via d'uscita verso
  un operatore.**
- Nello stesso caso la nota MEDIA continuava a citare `escalate_human`, cioè il
  prompt istruiva il modello su un'azione assente dall'enum.

## Decisione

### A. I criteri di handoff si configurano dalla UI

Tre chiavi nuove, risolte dalla cascata come tutto il resto:

| chiave | tipo | significato |
|---|---|---|
| `handoff.instructions.mode` | `extend` \| `replace` | i criteri del merchant si sommano ai default o li sostituiscono |
| `handoff.instructions.criteria` | lista di righe | «passa a un operatore quando…» |
| `handoff.instructions.exclusions` | lista di righe | «NON passare quando…» |

**`replace` esiste per un motivo concreto**: in assistenza reclami, recupero
crediti o legale i tre default sono rumore e farebbero scattare l'handoff a ogni
conversazione.

**Le eccezioni sono un asse separato, non criteri negati.** Sono la
generalizzazione della nota sui media, e coprono la classe di edge case più
frequente: i falsi positivi. Vengono rese come blocco negativo esplicito.

**Resa nel prompt.** Con la sola configurazione di default la resa è in prosa,
identica al prompt storico — nessun merchant esistente cambia comportamento.
Appena il merchant aggiunge un criterio si passa a un elenco puntato: una prosa
con otto condizioni in fila è illeggibile anche per un modello.

**`handoff.enabled=false` ora entra nel prompt.** Viene iniettato un blocco che
vieta esplicitamente di promettere un operatore, e l'azione sparisce dall'enum.
Chiude il difetto del cliente lasciato ad aspettare.

**`replace` senza criteri** rimuove del tutto l'azione: offrirla senza dire
quando usarla lascerebbe la decisione al modello, cioè l'opposto dello scopo.

### B. Un nome solo: handoff

Rename completo, wire name inclusi, con compatibilità in lettura ovunque il nome
vecchio sia un **dato** e non un identificatore.

| vecchio | nuovo |
|---|---|
| `escalation.enabled` | `handoff.enabled` |
| `escalation.handoff_message` | `handoff.message` |
| `escalation.silent_handoff` | `handoff.silent` |
| `escalation.critical_keywords` | `handoff.critical_keywords` |
| `escalation.sla_minutes` | `handoff.sla_minutes` |
| `escalation.phone_echo_pause_minutes` | `handoff.phone_echo_pause_minutes` |
| action kind `escalate_human` | `handoff_human` |

**Rinominare l'action kind è sicuro**: verificato che i dataset di fine-tuning
contengono solo coppie di testo naturale user/assistant prese da
`messages.content` (`workers/fine_tuning/collect.py`, `export.py`) — mai il JSON
strutturato, mai il system prompt. **Nessun modello è mai stato addestrato a
emettere `escalate_human`.** Era il rischio che avrebbe bloccato il rename.

### C. Tre livelli di compatibilità, tutti necessari

Nessuno dei tre basta da solo.

1. **`LEGACY_KEY_ALIASES` in `resolver._lookup`** — unico choke point della
   cascata, copre profilo/merchant/template e la forma sia annidata sia piatta.
   Senza: le override salvate diventano inerti e ogni merchant torna ai default
   al primo deploy, in silenzio.
2. **`validation_alias` su `BotConfigSchema.handoff` e sulle foglie rinominate** —
   `_StrictModel` ha `extra="forbid"` e il pannello **rispedisce l'intero bag a
   ogni salvataggio**. Senza: 422 al primo salvataggio per ogni merchant con una
   personalizzazione di handoff, su qualunque campo, anche non correlato.
3. **`normalize_action_kind` applicato subito dopo il parse** — un valore fuori
   dal `Literal` fa fallire `model_validate_json`, e il ramo di fallback
   restituisce `reply_text = raw`: **il cliente riceverebbe il JSON grezzo su
   WhatsApp.** `escalate_human` resta perciò nel `Literal`, accettato solo in
   lettura, e tradotto immediatamente.

Più la migrazione `0049_handoff_config_rename` (bag + `locked_keys`), che rende
gli alias rimovibili invece di eterni, e il bump di `RESOLVED_CACHE_KEY` a `v3`.

### D. Cosa NON viene rinominato, e perché

Questi nomi sono **dati scritti da terzi**, non identificatori nostri:

| nome | dove vive | perché resta |
|---|---|---|
| `conversation.escalated` | `analytics_events.event_type` | righe storiche già scritte: rinominarle falsifica lo storico, non rinominarle spezza le query |
| `conversation_escalated` | `automation_flows.trigger_type`, `automation_nodes.type` | automazioni disegnate dai merchant: un rename le rompe |
| `ESCALATED` | `conversations.current_state` | stati già persistiti |
| `escalate_human` dentro `allowed_actions` | `automation_nodes.config`, `conversation.playbook.actions.enabled` | letti tramite `normalize_action_kind` |

Rinominarli è possibile, ma è una migrazione dati separata con un profilo di
rischio diverso — non va mescolata a un cambio di nomenclatura.

### E. `handoff.critical_keywords` resta sotto `handoff.*` — con una riserva

È l'unica scelta discutibile dell'ADR: la chiave **non provoca l'handoff**,
seleziona il modello più capace. Metterla sotto `handoff.*` perpetua la
confusione che questo ADR vuole togliere. L'alternativa valutata era
`llm.critical_keywords`.

Si è scelto di tenerla, rendendo esplicito l'avvertimento nell'help della UI,
per non introdurre una sezione nuova nello stesso cambio. **Se la confusione si
ripresenta, spostarla è il passo giusto** e costa un alias in più.

## Conseguenze

- Un edge case di handoff si risolve dal pannello, non con un deploy.
- Il default resta byte-identico: nessun merchant esistente cambia comportamento.
- La valvola di sicurezza ora è reale e coperta da test.
- Un `handoff.enabled=false` smette di produrre promesse che nessuno mantiene.
- Restano due nomenclature nei dati persistiti (§D), documentate e circoscritte.
- Gli alias vanno rimossi quando la migrazione è applicata ovunque e i log non
  mostrano più letture legacy.
