# ADR 0036 — Una sola conversazione per lead: `get_active_or_reopen_latest` ovunque, `_latest_conversation_for_lead` ordinata come le altre

Data: 2026-09-17
Stato: accettato

## Contesto — correzione di ADR 0035

ADR 0035 (ieri) diagnosticava l'incidente Ghilea come un problema di
normalizzazione del prefisso telefonico. **Verificato sui dati reali di
produzione (Supabase, progetto `izhyypbjeqkqdxfnzzoo`), quella diagnosi era
sbagliata per questo incidente**: il numero del lead (`393208043592`) è
sempre stato corretto, in ogni riga, con prefisso, in tutte le tabelle. Il fix
di ADR 0035 resta una correzione valida in generale (un numero italiano senza
prefisso va comunque riparato), ma non è la causa di quanto segnalato dal
merchant.

La causa reale, ricostruita dal timeline esatto di `messages`/`conversations`
per il lead (`081073cd-7e43-48a1-aab6-7a7402bd233a`, merchant
`0e1fac1c-c1b2-4a84-86dc-c0eb68dbdbf2`): **quattro** righe `conversations`
distinte per lo stesso lead, create in un ciclo che si ripete ogni volta che
il trigger CRM riparte (21/07, poi di nuovo 14/09, 15/09×2, 17/09). Lo schema è
identico ogni volta:

1. Il trigger GHL (`crm_opportunity_created`) riparte per lo stesso lead (il
   deal ri-entra nella stage, o un workflow GHL lo ri-lancia — il lead ha già
   risposto piu' volte, non è un lead nuovo).
2. `_handle_crm_create` (`workers/conversation/handlers.py`) risolve la
   conversazione con `ConversationRepository.get_active()` — **solo
   `status='active'`**. La conversazione precedente è stata chiusa nel
   frattempo (idle-close), quindi non la trova e **ne crea una nuova, vuota**
   (`last_message_at = NULL`).
3. Il dispatcher (`automation_dispatch`, cron a 1 minuto) raccoglie l'evento
   ed esegue l'automazione. Per decidere DOVE mandare il messaggio,
   `_resolve_context` (subject_type="lead") chiama
   `_latest_conversation_for_lead` (`workers/automation/engine.py`), che
   ordinava per `last_message_at DESC NULLS LAST`. La conversazione appena
   creata al passo 2 ha `last_message_at = NULL` → **finisce in fondo**
   all'ordinamento. Vince la conversazione VECCHIA, quella con lo storico —
   l'automazione manda lì il template di primo contatto (di nuovo, a un lead
   che aveva già risposto tre volte).
4. Il lead risponde ("Si certo" / "Si ciao" / …). Il path WhatsApp inbound
   (`handle_inbound_persist` → `get_active_or_reopen_latest`) ordina per
   `started_at DESC` — sempre popolato, mai NULL — e trova correttamente la
   conversazione più NUOVA, quella creata al passo 2. La risposta finisce lì.

Automazione e risposta del lead, sistematicamente, su due righe diverse.
Verificato su tre cicli separati nei dati reali (14/09, 15/09, 17/09): stesso
schema, ogni volta.

## Decisione

### 1. `_handle_crm_create` riusa il thread esistente, non lo biforca

`get_active()` → `get_active_or_reopen_latest()` (stesso metodo già usato dal
path WhatsApp inbound e dall'eco del telefono, per lo stesso motivo: "un
messaggio nuovo da un contatto noto continua il thread esistente invece di
aprirne un duplicato"). Un trigger CRM che riparte su un lead che ha già una
conversazione — chiusa o non — la riapre, non ne crea una vuota. Effetto
collaterale utile, non richiesto ma corretto: il lead non riceve piu' lo
stesso messaggio di primo contatto ogni volta che il deal ri-entra in
pipeline, perché la conversazione mantiene lo storico e l'agente lo vede.

`get_active()` come metodo resta — usato anche da `handle_call_outcome`
(UC-03, takeover dopo chiamata falita), non toccato qui: stesso pattern
potenziale, ma fuori dallo scope di questo incidente specifico. Da rivedere se
si presenta un sintomo analogo su quel percorso.

### 2. `_latest_conversation_for_lead` ordina come le altre due query

Ordinamento cambiato da `last_message_at DESC NULLS LAST` a `started_at DESC`
— lo stesso campo che `get_active`/`get_active_or_reopen_latest` già usano.
Tre query che rispondono alla stessa domanda ("qual è la conversazione
attuale di questo lead/numero?") devono essere d'accordo, altrimenti una
manda e l'altra riceve su righe diverse anche quando esiste solo l'ambiguità
che questo ADR chiude. Con la fix del punto 1 questa ambiguità sparisce quasi
sempre nella pratica (non si crea più la riga vuota), ma la query resta
strutturalmente sbagliata finché non ordina per lo stesso criterio delle
altre due — difesa in profondità contro qualunque altro percorso che possa
ancora creare piu' di una conversazione per lo stesso lead.

## Conseguenze

* 1128 unit test verdi (+1: `test_contact_create_reuses_conversation_via_reopen_latest_not_get_active`
  in `tests/unit/test_ghl_event.py`, che fissa la chiamata al metodo giusto).
  Il fake `FakeConvRepo` ora espone sia `get_active` (ancora usato da
  `handle_call_outcome`) sia `get_active_or_reopen_latest`. ruff/ruff format
  puliti; mypy invariato (46 errori pre-esistenti nei due file, stesso conteggio
  prima/dopo — import-untyped e `no-any-return` non correlati alle righe
  toccate).
* **Non fatto qui, per lo stesso motivo di ADR 0035**: nessun test di
  integrazione (serve Postgres vivo, non disponibile in questa sessione) che
  verifichi realmente la semantica `ORDER BY` — verificato invece direttamente
  sui dati di produzione reali (vedi sopra), che è la controprova più forte
  possibile per questo bug specifico. Un test di integrazione dedicato resta
  un miglioramento sensato da aggiungere quando c'è un Postgres a disposizione.
* Nessuna migrazione: nessuna colonna nuova.
* **Le quattro conversazioni duplicate già esistenti per Ghilea non sono state
  toccate da questo ADR** — codice, non dati. La riconciliazione (fondere la
  storia in una sola riga superstite) è un'operazione separata, a mano, sui
  dati di produzione: vedi la nota operativa a parte per questo lead
  specifico.
* ADR 0035 non viene ritirato: la riparazione del prefisso italiano resta un
  fix corretto e utile in generale, semplicemente non è quello che ha causato
  questo incidente. Lezione per il futuro: verificare la diagnosi sui dati
  reali PRIMA di scrivere il fix, non dopo — qui è stato fatto al contrario,
  ed è costato un giro a vuoto.
