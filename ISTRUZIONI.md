# ISTRUZIONI — Collaudo end-to-end di Reloop AI

> Guida operativa per testare **tutti i 13 casi d'uso** del capitolato (`capitolato-tecnico.md`) più una
> serie di test trasversali, con prove oggettive. Pensata per chi **non** è sviluppatore: ogni test è nel
> formato **"scrivi X → il bot risponde Y → vai in <posizione> e trovi Z"**.
>
> **Questo file sostituisce `TESTS.md`** (obsoleto: descrive un vecchio collegamento GHL per-merchant e il
> 360dialog self-serve, che non sono più la realtà). Tutte le stringhe, le etichette e i valori qui sotto
> sono presi **dal codice attuale**.

---

## 0. Premessa & come leggere

- ✅ = verifica superata · ❌ = fallita · ⏭️ = saltato (manca un prerequisito).
- I test sono in ordine di dipendenza: fai prima il **Setup una-tantum** (§3), poi lo **Smoke** (§4), poi gli UC (§5).
- Le funzioni più recenti hanno una sezione dedicata: **Lavagnetta / Automazioni** (§6), **agente loop tool-use + consegna «umana»** (§7), **template WhatsApp** (§8), **handoff & correzioni del bot** (§9).
- Ogni UC ha fino a due **tracce**:
  - **Traccia A — WhatsApp reale**: scrivi davvero dal telefono al numero del merchant. È il test "vero".
  - **Traccia B — Playground**: stessa logica, stesso prompt, **simulazione senza inviare nulla** (utile come
    smoke veloce o quando non vuoi "sporcare" i dati reali). Il Playground è una *anteprima fedele* del
    flusso WhatsApp reale (ADR 0009/0010): mostra anche le **azioni** che il bot eseguirebbe (prenotazione,
    spostamento pipeline, scoring, escalation) e lo **stato lead** simulato.
- **⚠️ REGOLA D'ORO**: il bot risponde su WhatsApp **solo se** `bot.auto_reply_enabled` è **ON**. Di default
  è **OFF**. È la causa #1 di "il bot non risponde". Accendilo in *Configurazione* (§3.7) prima dei test
  conversazionali. Il Playground funziona anche con auto-reply OFF.

---

## 1. Ambiente & URL

| Servizio | URL | A cosa serve |
|---|---|---|
| **API** | `https://api-production-6ac7.up.railway.app` | Backend (chiamate `curl`, webhook) |
| **web-admin** (agenzia) | `https://web-admin-production-0a56.up.railway.app` | Pannello dell'agenzia (Wave) |
| **web-merchant** (cliente) | `https://web-merchant-production.up.railway.app` | Pannello del singolo merchant |

> Se gli URL non corrispondono più, prendili da **Railway → progetto → servizio → Settings → Domains**.

**Accessi tecnici usati in questa guida:**
- **Supabase** → SQL Editor (progetto `izhyypbjeqkqdxfnzzoo`) per le query di verifica (Appendice A).
- **Railway** → `railway logs` e `railway run` per leggere i log e far partire i job a richiesta (Appendice B).
- **GoHighLevel** → l'interfaccia del tuo account (Contacts, Opportunities, Calendar).

**Come ottenere il tuo token JWT** (serve solo per i test `curl`):
1. Fai login su web-admin (o web-merchant) nel browser.
2. Apri **DevTools → Application → Local Storage** → cerca la chiave che contiene `access_token` (sotto la
   voce Supabase) e copia il valore `access_token`.
3. Usalo come `-H "Authorization: Bearer <jwt>"`.

Verifica rapida dell'API:
```bash
curl https://api-production-6ac7.up.railway.app/health
# → {"status":"ok","environment":"production"}
```

---

## 2. Architettura in 6 righe (per non sbagliare i test)

- **Due livelli**: l'**agenzia** (web-admin) possiede molti **merchant** (web-merchant). L'isolamento dei dati
  è garantito dalla RLS sui claim del JWT (`tenant_id`, `merchant_id`).
- **GoHighLevel** si collega **una volta a livello agenzia** (marketplace). Le *location* installate compaiono
  in web-admin e si **collegano a un merchant**. **Un account GHL = (tipicamente) una location = un merchant.**
  Non esiste un OAuth GHL per singolo merchant.
- **WhatsApp** passa da un **router** esterno (di Wave/Relooptech). Il merchant collega il proprio numero in
  autonomia dal suo pannello (Embedded Signup 360dialog). Il canale di messaggistica è WhatsApp; GHL serve
  per contatti/opportunità/calendario.

---

## 3. Setup una-tantum

Fallo in questo ordine. Salta i passi già fatti.

### 3.1 — Abilita l'Auth hook di Supabase (prerequisito assoluto)
Senza, i JWT non portano `tenant_id`/`merchant_id`/`role` e **ogni pagina è vuota**.
- Supabase → **Authentication → Hooks → Custom Access Token hook → Add hook**.
- Tipo **Postgres**, schema `public`, funzione `custom_access_token_hook` → **Enable** → Save.

### 3.2 — Crea l'admin agenzia (solo la prima volta)
- Vai su **web-admin → /login** e registrati con l'email che vuoi come admin (es. `admin@…`).
- Al primo accesso il sistema chiama `POST /auth/bootstrap`: crea il tenant agenzia e ti promuove a
  `agency_admin`. Atterri su **/dashboard**.
- Verifica i claim:
  ```bash
  curl -H "Authorization: Bearer <jwt>" https://api-production-6ac7.up.railway.app/auth/whoami
  # → {"actor_id":"…","tenant_id":"…","role":"agency_admin","merchant_id":null}
  ```
  Se ottieni `403 missing_tenant_claim`: l'hook (§3.1) non è attivo. Esci e rientra per rigenerare il JWT.

### 3.3 — Collega GoHighLevel a livello agenzia
- web-admin → **Integrazioni** → card **"GoHighLevel — Agenzia"** → pulsante **"Collega agenzia GHL"**.
- Approva il consenso su GHL. Torni con `?provider=ghl_agency&status=connected` e lo stato diventa **"Connessa"**.
- *(Prerequisito lato app GHL Marketplace: Redirect URI = `…/integrations/crm/oauth/callback` — usa "crm",
  non "ghl".)*

### 3.4 — Installa l'app GHL sulla tua location e collegala al merchant
- Dal Marketplace GHL, **installa l'app** sul tuo account/location. GHL invia un webhook **INSTALL** a
  `POST /webhooks/ghl/marketplace`; la location compare in web-admin → **Integrazioni** → tabella
  **"Location installate"** con stato **"In attesa"**.
- Crea prima il merchant (§3.5), poi torna qui: nella riga della location scegli il merchant dal menu e premi
  **"Collega"**. Lo stato passa a **"Attiva"**. (Per disfare: **"Scollega"**.)

### 3.5 — Crea il merchant
- web-admin → **Merchant** → **"+ Nuovo merchant"** → nome + slug → **"Crea merchant"**.
- Clicca il merchant per aprire `/merchants/{id}`.

### 3.6 — Invita l'utente merchant
- Nella pagina del merchant → card **"Utenti merchant"** → crea utente con **email + password** (min 8
  caratteri). *Nessuna email viene inviata*: comunica tu le credenziali.
- L'utente fa login su **web-merchant → /login**.

### 3.7 — Collega WhatsApp e compila la configurazione (come merchant)
Accedi a **web-merchant** con l'utente del merchant.
1. **Integrazioni** → card **"WhatsApp (360dialog)"** → collega il numero (procedura ufficiale 360dialog).
   Al termine lo stato diventa **"Connesso"**. La card GHL mostrerà **"Collegato tramite agenzia · Location: …"**.
2. **Bot → Configurazione** (`/bot/config`):
   - **Profilo attività**: nome, settore, descrizione, offerta, orari, sede, note prezzi, sito. *(Il system
     prompt del bot nasce da qui.)*
   - **Bot**: lingua (`it`), tono, e **accendi `auto_reply_enabled` (ON)** ⚠️.
   - **Booking (UC-02)**: scegli il **calendario** dal menu a tendina (popolato da GHL) o incolla l'ID.
   - **Pipeline (UC-04)**: incolla `default_pipeline_id`, `new_stage_id`, `qualified_stage_id` (prendili da
     GHL → Settings → Pipelines).
   - **Scoring (UC-05)**: lascia i default (hot=80, cold=30) o regola.
3. *(Opzionale, per UC-07)* **Bot → Knowledge base**: carica un PDF/DOCX e attendi stato **"indexed"**.

---

## 4. Smoke test (5 minuti)

1. `curl …/health` → `{"status":"ok"}`.
2. Login web-admin → **/dashboard** senza errori in console.
3. Login web-merchant → **Bot → Playground** → scrivi `ciao` → il bot risponde in italiano in pochi secondi.
4. In **Configurazione** cambia il nome attività → torna nel Playground e chiedi `Cosa fate?` → la risposta
   cita il nuovo nome (la cache si aggiorna in ~60s).

Se questi 4 passano, il cuore della piattaforma (JWT, RLS, orchestratore, model router, system prompt,
cascade config, Realtime) funziona — senza ancora toccare WhatsApp/GHL.

---

## 5. Test per caso d'uso

> Per ogni Traccia A serve: WhatsApp del merchant **Connesso** + `auto_reply_enabled` **ON**. Per le verifiche
> SQL vedi Appendice A; per far partire i job a richiesta vedi Appendice B.

### UC-01 — First Response Istantaneo
**Cosa prova**: il bot risponde su WhatsApp in pochi secondi, in modo naturale, e qualifica.

**Traccia A (WhatsApp reale)**
1. Dal tuo telefono scrivi al numero del merchant: **`Ciao, cosa fate?`**
2. **Atteso (Y)**: entro ~10s arriva una risposta in italiano che **cita nome/settore/offerta** dal profilo
   attività (es. *"Ciao! Siamo {nome}, ci occupiamo di {settore}…"*).
3. **Verifica (Z)**:
   - web-merchant → **Conversazioni**: compare il thread, i messaggi scorrono live.
   - web-admin → **Inbox**: stesso thread (l'admin vede tutti i merchant).
   - SQL: `messages` ha una riga `role='user'` e una `role='assistant'` (vedi A.1).

**Traccia B (Playground)**: scrivi `Ciao, cosa fate?` → la reply riflette il profilo. (Il Playground **non**
scrive su `conversations`.)

**Variante negativa (gate auto-reply)**: in **Configurazione** metti `auto_reply_enabled` **OFF**, scrivi un
messaggio su WhatsApp → **il messaggio si salva ma il bot NON risponde**. SQL: trovi solo la riga `role='user'`.
Riaccendi l'auto-reply dopo il test.

---

### UC-02 — Booking Autonomo
**Prerequisiti**: calendario configurato (§3.7), GHL collegato.

**Traccia A**
1. In conversazione scrivi: **`vorrei prenotare giovedì alle 15`**
2. **Atteso (Y)**: *"Perfetto, ho prenotato per te l'appuntamento del **giovedì alle 15:00**. Ti invieremo il
   promemoria."*
3. **Verifica (Z)** in **GoHighLevel**:
   - **Calendar**: nuovo evento giovedì 15:00–15:30 (durata default 30 min).
   - **Contacts**: il contatto del lead esiste (creato/aggiornato dal bot).
   - **Opportunities**: una nuova opportunità nella pipeline configurata, allo stage `new_stage_id`.
4. **Verifica DB** (Appendice A.3): riga in `appointments` con `source='bot'` e `ghl_appointment_id`;
   `leads.meta` con `ghl_opportunity_id`; evento `booking.created` in `analytics_events`.

**Sotto-test — proposta slot**: scrivi **`quando siete liberi?`** → *"Ecco le prime disponibilità: • … • … • …
Fammi sapere quale preferisci."* (legge gli slot liberi da GHL su 14 giorni).

**Sotto-test — slot occupato**: prenota uno slot già preso → *"Quello slot non è più disponibile. Ti
suggerisco: • … Fammi sapere quale preferisci."*

**Sotto-test — reminder appuntamento**: il job `send_appointment_reminders` (ogni 30 min, per appuntamenti
entro 24h) invia *"Promemoria: hai un appuntamento {data} alle {ora}. A presto!"*. Per non aspettare, prenota
uno slot **entro le prossime 24h** e lancia il job a richiesta (Appendice B). Verifica `appointments.meta`
con `reminder_sent_at` valorizzato (A.3).

**Traccia B (Playground)**: scrivi `voglio prenotare domani alle 15` → tra gli **eventi** compare
`book_slot` (*"Prenotazione simulata …"*) e lo **stato** passa a `booked=true` (nessuna scrittura su GHL).

---

### UC-03 — Gestione Senza Risposta
**Cosa prova**: il bot riprende il contatto quando il lead non risponde o quando una **chiamata non va a buon fine**.

**Parte (a) — silenzio in chat**
1. In **Configurazione** abbassa `no_answer.first_reminder_min` a **30** (minimo consentito).
2. Manda un inbound, lascia che il bot risponda, poi **smetti di rispondere**.
3. Aspetta il tick del cron (`followup_no_answer`, ogni 15 min) **oppure** lancialo a richiesta (Appendice B).
4. **Atteso (Y)** sul telefono: *"Ciao! Eri ancora interessato? Se vuoi posso aiutarti a completare la
   richiesta."* Il secondo reminder (dopo `second_reminder_min`, default 24h) è *"Facciamo un ultimo tentativo
   — se vuoi riprendere la conversazione, rispondi pure."*
5. **Verifica (Z)**: `conversations.meta` ha `reminders_sent` incrementato e `last_reminder_at` valorizzato (A.5).
6. Ripristina la soglia a 120 dopo il test.

**Parte (b) — chiamata fallita** *(richiede i webhook dati GHL — vedi §12 Limitazioni)*
1. In GHL registra/logga sul contatto una chiamata con esito **"no answer"** (o busy/failed/voicemail/no_show).
2. GHL invia l'evento a `POST /webhooks/ghl/marketplace` → `handle_ghl_event` crea/riusa una conversazione e
   la marca `meta.origin='call_failed'`, rendendola candidata al follow-up.
3. **Verifica (Z)**: `conversations.meta` con `origin='call_failed'` (A.5); al tick successivo del cron arriva
   il primo reminder come nella parte (a).
- **Alternativa senza webhook GHL**: lancia l'evento a mano (Appendice B, snippet "call failed").

---

### UC-04 — Spostamento Pipeline Automatizzato
**Prerequisito**: UC-02 eseguito sullo stesso lead (così esiste l'opportunità su `leads.meta`).

**Traccia A**
1. Dopo la prenotazione scrivi: **`ok confermo, sono interessato a iniziare`**
2. **Atteso (Y)**: il bot prosegue la conversazione (lo spostamento è un'azione "silenziosa", non un messaggio
   dedicato).
3. **Verifica (Z)** in **GoHighLevel**:
   - **Opportunities**: l'opportunità è passata allo stage **`qualified_stage_id`**.
   - **Contacts → tab Notes**: nota che inizia con
     `[Reloop AI] Lead spostato in pipeline dalla conversazione WhatsApp.` seguita da `Motivo:`, `Sentiment:`,
     `Nome:`, `Email:` (quando disponibili).
4. **Verifica DB** (A.4): `leads.pipeline_stage_id` aggiornato; evento `pipeline.moved` in `analytics_events`
   con `variant_id` valorizzato.

**Traccia B (Playground)**: con un lead "caldo", tra gli **eventi** compare `move_pipeline`
(*"Spostato in pipeline → …"*).

---

### UC-05 — Qualificazione Predittiva con Lead Scoring
**Cosa prova**: a ogni turno il bot assegna un punteggio cumulativo.

**Traccia A/B** (vale anche nel Playground, dove lo scoring è identico al reale):
1. Conduci 3–4 turni fornendo via via: **nome**, **email**, **budget**, e una **richiesta di prenotazione**.
   Es.: `Mi chiamo Mario` → `la mia email è mario@…` → `il budget è circa 2000€` → `vorrei prenotare`.
2. **Atteso (Y)**: lo score sale (es. ~50–65 dopo qualche turno). Un turno neutro tipo `ok` **non** fa
   crollare lo score (i segnali di contenuto sono cumulativi).
3. **Verifica (Z)**:
   - SQL (A.6): `leads.score` cresce; `leads.score_reasons` contiene `has_name`, `has_email`,
     `asked_for_booking`, `has_budget`…; `leads.meta.content_signals` accumula i segnali di contenuto.
   - UI: web-merchant → **Dashboard** → grafico **"Distribuzione score lead"**.
   - Classificazione: score ≥ **80** = hot, ≤ **30** = cold, in mezzo = warm.

---

### UC-06 — Riattivazione Database Dormiente
**Cosa prova**: sequenze automatiche verso contatti inattivi, con opt-out.

**Test riattivazione**
1. Rendi "dormiente" un lead via SQL (A.7): porta `conversations.last_message_at` a ~100 giorni fa
   (soglia default 90).
2. Lancia il job `reactivate_dormant_leads` a richiesta (Appendice B).
3. **Atteso (Y)** sul telefono: *"Ciao! È passato un po' — se l'interesse è ancora vivo, possiamo riprendere
   da dove eravamo?"* (tentativi 2 e 3 hanno testi diversi). Se imposti un testo personalizzato in config, lì
   `{name}` viene sostituito col nome del lead.
4. **Verifica (Z)**: evento `lead_reactivation.sent`; `leads.meta` con `reactivation_attempts` e
   `last_reactivation_at` (A.7).

**Test opt-out (STOP)**
1. Dal telefono scrivi esattamente **`STOP`** (valgono anche: `cancella`, `annulla`, `disiscrivi`, `unsubscribe`…).
2. **Atteso (Y)**: il bot **non** risponde più automaticamente a quel lead.
3. **Verifica (Z)**: `leads.opted_out_at` valorizzato (A.8) ed evento `lead.opted_out`. Il lead è **escluso**
   dalle riattivazioni successive.

> ⚠️ Fuori dalla finestra 24h (i dormienti lo sono per definizione) l'invio richiede un **template WhatsApp
> approvato** (flow `reactivation`). Senza template il job **salta in modo pulito** (non è un errore): vedrai
> `lead_reactivation.skipped`.

---

### UC-07 — Knowledge Base
**Traccia**
1. web-merchant → **Bot → Knowledge base** → **"Carica e indicizza"** un PDF (o **"Indicizza da URL"**).
2. **Atteso (Y)**: lo stato del documento passa a **"indexed (N chunk)"** entro ~30s.
3. Test RAG: nel **Playground** chiedi qualcosa la cui risposta è **dentro** il documento.
4. **Verifica (Z)**: la risposta usa il contenuto del documento; nella risposta del Playground compaiono i
   `retrieved_chunks` (i pezzi recuperati con il loro punteggio). SQL (A.9): `knowledge_base_docs.status='indexed'`
   e righe in `kb_chunks`.
5. Prova i pulsanti **"Re-indicizza"** ed **"Elimina"** (l'eliminazione rimuove anche i chunk).

> Se il recupero "manca" la risposta giusta, abbassa `rag.min_score` (default 0.7) in Configurazione.

---

### UC-08 — Playground e Addestramento
**Traccia**
1. web-merchant → **Bot → Playground**. Scrivi qualche messaggio.
2. **Atteso (Y)**:
   - Risposta del bot identica a come risponderebbe su WhatsApp (stesso prompt/impostazioni).
   - Pannello **"Stato lead simulato"**: score, nome/email catturati, `booked`, ecc. che evolvono turno dopo turno.
   - Quando l'input lo richiede, compaiono **eventi simulati**: `book_slot`, `move_pipeline`, `update_score`,
     `escalate_human` (senza scrivere su DB/GHL/WhatsApp).
3. Pannello **"Regole"**: aggiungi una regola (es. `Non promettere sconti`). Si applica **subito** all'anteprima.
4. Premi **"Salva regole"** → diventa persistente (finisce in `bot.system_prompt_additions`).
5. **Verifica (Z)**: invia in Playground (o su WhatsApp reale) un input che tenterebbe di violare la regola →
   il bot la rispetta.

---

### UC-09 — A/B Testing Bot
**Traccia**
1. web-merchant → **Bot → A/B testing** → **"+ Nuovo esperimento"**.
2. Compila: nome, metrica primaria (default `booking.created`), e **due prompt diversi** (control vs variant),
   split **50/50** → **"Crea esperimento"** → poi **"Avvia"**.
3. Conduci ~10 conversazioni da numeri/lead diversi.
4. **Atteso (Y)**: clic su **"Metriche"** → vedi assegnazioni e conversioni per variante; un banner di
   significatività (z-test) dichiara *"Differenza significativa…"* o *"Campione insufficiente…"*; quando
   concluso, il **vincitore**. Puoi premere **"Ferma"**.
5. **Verifica (Z)**: SQL (A.10) — `ab_assignments` mostra che **lo stesso lead resta sulla stessa variante**
   (stickiness); gli eventi di conversione portano il `variant_id` corretto (così le metriche non sono più a zero).

---

### UC-10 — Bot Default Agenzia (template)
**Traccia** (come **agency_admin** su web-admin)
1. web-admin → **Template bot** (`/templates`) → **"+ Nuovo template"**.
2. Imposta un valore, es. **`rag.top_k = 7`**, spunta **"Blocca"** accanto alla chiave, e marca il template
   come **default** → salva.
3. **Verifica (Z)** su un merchant senza override:
   ```bash
   curl -H "Authorization: Bearer <admin-jwt>" \
     "https://api-production-6ac7.up.railway.app/bot-config/<merchant_id>/resolved"
   # → rag.top_k = 7 (ereditato dal template)
   ```
   Il merchant che prova a cambiare `rag.top_k` nella sua **Configurazione** trova il campo **bloccato**
   (la `PUT …/overrides` risponde con `locked_keys_skipped`). Prova anche **"Elimina"** il template.

---

### UC-11 — Dashboard Analytics Merchant
**Traccia**
1. web-merchant → **Dashboard**.
2. **Atteso (Y)**: KPI **"Lead totali"**, **"Lead hot"**, **"Tasso risposta"**, **"Booking rate"**, e il
   grafico **"Distribuzione score lead"**. Filtri: **periodo** (Ultimi 7/30/90 giorni) e **campagna**
   (*"Tutte le campagne"* + le campagne rilevate).
3. **Verifica (Z)**: dopo aver generato traffico (UC-01/02/05), i numeri **salgono in tempo reale** (Supabase
   Realtime). Confronta con SQL (A.11).

> Le campagne arrivano dal parametro click-to-WhatsApp (referral) catturato sul primo inbound e salvato su
> `leads.campaign`.

---

### UC-12 — Dashboard Unificata Admin Agenzia
**Traccia**
1. web-admin → **Dashboard**.
2. **Atteso (Y)**: KPI aggregati **"Lead totali"**, **"Merchant attivi"**, **"Messaggi ricevuti"**,
   **"Booking creati"**, e la tabella **"Ranking merchant (conversione)"** ordinata per conversione. La riga
   è **cliccabile** e porta a `/merchants/{id}` (drill-down sul singolo merchant, con i suoi KPI).
3. **Verifica (Z)**: i totali combaciano con la somma dei merchant del tenant (A.12).

> *Export CSV*: l'endpoint esiste (`POST /analytics/exports`) ma il **pulsante in UI è ancora parziale** (§12).

---

### UC-13 — Report Obiezioni e Insight
**Traccia**
1. Conduci una conversazione che contenga obiezioni, es.:
   - `è troppo caro` → categoria **prezzo**
   - `non mi fido` → **fiducia**
   - `non ho fretta` → **tempistiche**
   - `il vostro concorrente è più bravo` → **concorrenza**
   - `non mi serve` → **necessita**
2. Estrai le obiezioni:
   - **Automatico**: il cron `close_idle_conversations` chiude le conversazioni inattive (default 120 min) e
     lancia l'estrazione. Per non aspettare, lancia il job a richiesta (Appendice B).
   - **A richiesta**:
     ```bash
     curl -X POST -H "Authorization: Bearer <jwt>" \
       https://api-production-6ac7.up.railway.app/reports/objections/extract/<conversation_id>
     ```
3. **Atteso (Y)**: web-merchant → **Obiezioni** (`/reports/objections`) mostra un **grafico a barre** per
   categoria, una **heatmap** per giorno/categoria, e **citazioni** di esempio. Filtri: periodo e variante A/B.
4. **Verifica (Z)**: SQL (A.13) — righe in `objections` con `category`, `quote`, `severity`. Vista agenzia
   aggregata: web-admin → **Obiezioni** (`/reports/objections/agency`).

---

## 6. Lavagnetta — Automazioni (flussi a grafo)

web-merchant → **Messaggistica → Automazioni** (`/automazioni`). La **lavagnetta** è un editor visuale a grafo
(React Flow): colleghi un **trigger** a **condizioni** e **azioni**. È il modello unificato che ha sostituito i
vecchi "Flussi" lifecycle e la rotta `/flussi` (ora rimossa). L'elenco ha due gruppi:
- **Flussi di sistema**: 4 flussi sempre presenti (seminati al primo accesso), con **trigger bloccato** (non
  eliminabile). Sono guidati dagli scheduler; puoi solo aggiungere condizioni e azioni «Invia»/«Risposta AI»
  per personalizzare gli invii. **Attivandolo, il flusso sostituisce il testo predefinito** del lifecycle.
- **Automazioni personalizzate**: le crei tu. Sono **event-driven** e girano **solo se sono "Attive"**.

### 6.1 — Anatomia (cosa trovi sulla lavagnetta)
- **Elenco** (`/automazioni`): ogni card mostra nome, tipo di trigger, conteggio nodi/collegamenti, un **badge**
  (**Sistema** / **Attiva** / **Bozza**), il pulsante **"Apri sulla lavagnetta"** e **"Elimina"** (solo per le
  personalizzate). In alto: **"Nuova automazione"**.
- I **4 flussi di sistema** (etichetta ← `system_key`): **Nessuna risposta** (`no_answer`), **Riattivazione
  dormienti** (`reactivation`), **Promemoria appuntamento** (`booking_reminder`), **Primo contatto**
  (`first_contact`).
- **Editor**: palette a sinistra (**Trigger**, **Condizioni**, **Azioni**), canvas al centro (pan + zoom +
  MiniMap), pannello di configurazione a destra. **Le condizioni hanno due uscite: «sì» e «no».** In basso
  **"Annulla"** / **"Salva"** e la spunta **"Attiva"**.

**Palette — Trigger** (uno solo per automazione): `Messaggio ricevuto` · `Nessuna risposta` ·
`Prenotazione creata` · `Prenotazione fallita` · `Lead dormiente`.

**Palette — Condizioni**: `Temperatura lead` (Caldo/Tiepido/Freddo) · `Punteggio lead` (≥, ≤, …) ·
`Finestra 24h aperta` · `Fascia oraria (UTC)` (Dalle/Alle) · `Messaggio contiene` (parole chiave) ·
**`Se (E / O)`** = gruppo composito: **Combinazione** «Tutte (E)» o «Almeno una (O)» + più clausole, ognuna
con spunta **«Nega (NOT)»**.

**Palette — Azioni**: `Invia messaggio` (Politica finestra 24h: *Auto* / *Solo template approvato* / *Solo
testo libero*) · `Risposta AI` (Obiettivo, Istruzioni extra, **Azioni AI consentite** multiselect, Template di
fallback, Modello) · `Aggiorna lead/CRM` (Campo: *Tag* / *Punteggio (delta)* / *Campo personalizzato* +
**Sincronizza su GHL**) · `Passa a operatore` (Motivo) · `Attendi` (Minuti). *(Le azioni «legacy»
`Invia template`/`Invia testo` restano per le personalizzate ma sono nascoste nei flussi di sistema.)*

### 6.2 — Crea e attiva un'automazione personalizzata (Traccia A)
1. **"Nuova automazione"** → trascina dalla palette **"Messaggio ricevuto"**: appare il nodo trigger (verde).
2. Aggiungi **"Messaggio contiene"** (parole: `prezzo, costo`); collega il trigger al nodo (trascina dal
   pallino in basso al pallino in alto).
3. Dal ramo **«sì»** della condizione collega un'azione **"Risposta AI"** (Obiettivo: *"rispondi al dubbio
   sul prezzo e proponi una call"*).
4. Rinomina in `Test prezzo`, spunta **"Attiva"**, **"Salva"** → torni all'elenco con badge **Attiva**.
5. **Verifica struttura**: riapri con **"Apri sulla lavagnetta"** → nodi e collegamenti sono persistiti.

**Variante negativa (validazione trigger)**: aggiungi un **secondo** trigger → compare *"Un'automazione
richiede esattamente un nodo trigger (ora: 2)."* e **"Salva"** è disabilitato. Rimuovine uno per riabilitarlo.
Lo stesso vale con **zero** nodi.

### 6.3 — «Se (E / O)» composito
Aggiungi **"Se (E / O)"** → **Combinazione** `Tutte (E)` → **"+ Aggiungi condizione"** due volte: clausola 1
`Punteggio lead ≥ 60`, clausola 2 `Finestra 24h aperta` con **«Nega (NOT)»** spuntata. Atteso: il ramo «sì»
parte solo se *score ≥ 60* **E** *NON* in finestra 24h. Con `Almeno una (O)` basta una clausola vera.

### 6.4 — Come "parte" un'automazione (il motore)
Il motore è **event-driven**: il cron **`automation_dispatch`** (ogni minuto) fa il *tail* di `analytics_events`
e accoda un `automation_run` per ogni automazione **personalizzata + Attiva** sottoscritta a quell'evento.
Mappa **evento → trigger**:

| Evento (`analytics_events.event_type`) | Trigger lavagnetta |
|---|---|
| `message.received` | Messaggio ricevuto |
| `booking.created` | Prenotazione creata |
| `booking.failed` | Prenotazione fallita |
| `reminder.sent` | Nessuna risposta |
| `lead_reactivation.sent` | Lead dormiente |

I **flussi di sistema sono esclusi** dal dispatcher (li guidano gli scheduler) → niente doppia esecuzione.
Altre regole: **dedup 24h** per coppia (flusso, evento); **una sola «Risposta AI» per run** (anti-loop);
i nodi **«Attendi»** rinviano il resto del flusso e lo ri-accodano dopo i minuti indicati.

**Test end-to-end (Traccia A reale)**
1. Crea automazione: trigger **"Messaggio ricevuto"** → azione **"Aggiorna lead/CRM"** (Campo *Tag*, Valore
   `vip`, **Sincronizza su GHL** ON). **Attiva** + Salva.
2. Scrivi su WhatsApp dal telefono del lead → si emette `message.received`.
3. **Atteso (Y)**: al tick successivo del cron (entro ~1 min) l'automazione gira. Per non aspettare, **forza il
   dispatch** a richiesta (Appendice B, snippet "automazioni").
4. **Verifica (Z)**: nei **log del worker** compaiono `automation.dispatch` (con `dispatched=…`) e
   `automation.run` (con `automation_id`, `sent`, `deferred`); il **tag `vip`** appare sul contatto in GHL.
   *(Non esiste una tabella `automation_runs`: l'audit è nei log; lo stato si verifica dagli effetti — tag,
   `leads.score`, `conversations.handoff_at`, messaggi inviati.)*

### 6.5 — Comportamento delle azioni "infrastrutturali"
- **Risposta AI** (`ai_reply`): genera e invia **un** messaggio proattivo mirato, rispettando la **finestra 24h**
  (testo libero entro, template di fallback fuori); può dispatchare le sole **Azioni AI consentite** che hai
  spuntato (es. `Prenota appuntamento`, `Avanza in pipeline`). **Guard**: se la conversazione è in **takeover**
  (handoff attivo) la Risposta AI viene **saltata** (log `automation.ai_reply.skipped reason=takeover`).
- **Aggiorna lead/CRM** (`set_lead_field`): *Punteggio (delta)* → applica un delta allo score (`update_score`);
  *Tag* / *Campo personalizzato* → propagati su **GHL** se **«Sincronizza su GHL»** è ON. Senza GHL collegato →
  **salta in modo pulito** (log `…skipped reason=no_ghl`). Non invia nessun messaggio.
- **Passa a operatore** (`human_handoff`): mette la chat in **gestione umana** (takeover): `auto_reply=false`,
  `handoff_at` valorizzato; l'AI **smette di rispondere** (vedi §9).

> Stringhe ed etichette verbatim della lavagnetta: **Appendice C**. Query/log di verifica: **Appendice B**.

---

## 7. Agente: loop tool-use (anti-falsa-conferma) + consegna «umana»

L'agente ora ragiona come **Amalia**: nello **stesso turno** può chiamare **strumenti di lettura** per ancorarsi
a dati reali **prima** di rispondere — così non promette più uno slot occupato (ADR 0013).
**Gate** (Bot → Configurazione, sezione **agent**): `agent.tool_use_enabled` (default **ON**) ·
`agent.max_tool_iterations` (default **3**, range 1–5; **1 = single-shot** classico, niente strumenti).

### 7.1 — Anti-falsa-conferma (Playground + reale)
**Prerequisito**: **GHL collegato** (gli strumenti leggono calendario/appuntamenti dal mirror GHL).
1. Prenota uno slot (UC-02), poi prova a prenotare lo **stesso** slot **occupato**: `prenota domani alle 15`.
2. **Atteso (Y)**: il bot **non** dice subito *"ho prenotato"*. Manda prima una **frase di passaggio**
   (*"un attimo che verifico"* / *"procedo subito e ti confermo"*); a metà turno gira `check_availability`,
   scopre che è occupato e la risposta **finale** propone alternative reali:
   *"Lo slot richiesto … NON è libero. Disponibilità reali più vicine: …"*.
3. **Sotto-test disponibilità**: `quando siete liberi?` → `check_availability` legge le disponibilità **vere**
   da GHL e propone slot reali.
4. **Sotto-test appuntamento**: `vorrei spostare il mio appuntamento` → `lookup_appointment` recupera
   l'appuntamento e il bot risponde con i **dettagli reali** prima di proporre lo spostamento.

**Variante negativa**: porta `agent.max_tool_iterations` a **1** → torna **single-shot** (nessuna verifica
mid-turn). **Senza GHL**: *"Calendario non collegato: non posso verificare le disponibilità reali."* e il bot
non promette nulla.

> Funziona identico nel **Playground** (stesso system prompt del flusso reale): se il GHL è collegato, vedi gli
> stessi strumenti di lettura agire prima della risposta.

### 7.2 — Fail-safe su errore dell'AI
Se l'LLM va in errore, il cliente riceve **sempre** cortesia + handoff:
*"Grazie per il tuo messaggio! Lo passo subito a un nostro operatore che ti risponderà a brevissimo."* e la
conversazione passa in **gestione umana** (`handoff_reason='ai_error'`). **Verifica**: riga `role='assistant'`
col testo di cortesia, `conversations.handoff_at` valorizzato, evento `conversation.escalated` con
`reason='ai_error'`. *(Override per merchant: `escalation.handoff_message`.)*

### 7.3 — Staleness inbound (niente risposte a vecchi backlog)
`schedule.inbound_staleness_min` (default **10** min, `0` = disattiva). Un inbound **più vecchio** della soglia
(es. backlog accumulato durante un downtime) viene **salvato ma non risposto**, così il bot non risponde fuori
contesto. **Verifica**: in `messages` resta solo la riga `role='user'`; log `uc01.auto_reply_skipped`
`reason='stale'`. *(Il timestamp arriva dal webhook Meta, propagato al worker.)*

### 7.4 — Consegna «umana» (debounce / typing / multi-bolla)
Tutti i default `delivery.*` sono **ON**: **debounce** 8 s (accorpa messaggi ravvicinati), **typing indicator**
+ spunte di lettura, **pausa di digitazione** 1–6 s (proporzionale alla lunghezza, con jitter deterministico),
**multi-bolla** max 2 (split a 600 caratteri).
- **Debounce**: manda **3 messaggi in < 8 s** → il bot risponde **una volta sola** (turno unico su tutti e 3).
  DB: 3 righe `role='user'`, **1** risposta. Log `uc01.debounced` poi `uc01.handled`.
- **Typing/pausa**: sul telefono vedi le **spunte di lettura** e *"sta scrivendo…"* prima della risposta.
- **Multi-bolla**: una risposta lunga (> 600 char) arriva in **2 bolle** separate; in **Conversazioni** resta
  **una sola** riga assistant (testo intero).

### 7.5 — Throttler 360dialog + Retry-After
Il client 360dialog limita ~**8 msg/s per canale** e rispetta l'header **Retry-After** sui `429` (backoff).
Osservabile soprattutto nei **log** (`d360.send.retry`, `d360.send_failed`) — non c'è un test "telefono" diretto.

### 7.6 — Sezioni di configurazione sbloccate
Bot → **Configurazione** ora espone le sezioni operative prima nascoste: **No answer (UC-03)**,
**Reactivation (UC-06)**, **Scoring (UC-05)**, **Booking (UC-02)**, **Consegna (tono umano)**, **agent**,
**Escalation**. I campi mostrano i badge **Inherited / Customized / Locked**; sotto **impersonazione agenzia**
compaiono come **Override agenzia** (e l'agenzia può **bloccare** una chiave per il merchant).

---

## 8. Template WhatsApp — validazione

web-merchant → **Messaggistica → Template WhatsApp** (`/whatsapp-templates`). Servono per scrivere **fuori dalla
finestra 24h** (riattivazioni, follow-up, promemoria). Il backend **valida l'intero ruleset Meta prima**
dell'invio, così non aspetti ore per scoprire un rifiuto.

### 8.1 — Crea, valida, invia
1. **"Nuovo template"** → compila **Scopo**, **Categoria** (`UTILITY`/`MARKETING`/`AUTHENTICATION`), **Lingua**,
   **Corpo del messaggio** (con variabili `{{1}}`, `{{2}}`…), **Valori di esempio per le variabili**, e
   facoltativi **Intestazione**, **Footer**, **Pulsanti**. A lato, **anteprima a bolla WhatsApp** in tempo reale.
2. **"Salva come bozza"** (non invia) **oppure** **"Crea e invia per approvazione"** (→ 360dialog/Meta).
3. **Stati**: **Bozza → In approvazione → Approvato / Rifiutato**. **"Sincronizza stato"** aggiorna da Meta.
   Un template **Rifiutato** si può **"Modifica"**re e re-inviare (riparte da Bozza).

### 8.2 — Regole che il validatore blocca (con esempi)
Prova a creare/validare un template che viola una regola → ottieni **errori** (bloccano) o **warning**
(consigli). Endpoint **read-only** per pre-controllare senza creare nulla:
`POST /whatsapp-templates/validate`.

| Cosa provi | Esito atteso |
|---|---|
| Variabili adiacenti: `Ciao {{1}}{{2}}` | **errore** (le variabili vanno separate da testo) |
| Corpo che **inizia** con `{{1}}` | **errore** |
| Variabili nel corpo ma **senza** valori di esempio | **warning** |
| **Footer** > 60 caratteri o con `{{1}}` | **errore** |
| Pulsante **URL** non `https://` | **errore** |
| Pulsante telefono non in formato `+E.164` | **errore** |
| `AUTHENTICATION` con **URL nel corpo** | **errore** |
| **Intestazione immagine** | **errore** (non supportata in V1) |
| Parole promozionali (`sconto`, `offerta`) in `UTILITY` | **warning** (valuta `MARKETING`) |
| Lingua sconosciuta (es. `xx_XX`) | **warning** (non errore) |

**Persistenza esempi** (migrazione 0026): crea una bozza con esempi, riaprila → i **Valori di esempio** sono
salvati; modificali e ri-salva → restano persistiti.

---

## 9. Handoff & correzioni del bot

### 9.1 — Pausa AI / takeover (`ai_disabled_until`)
In **Conversazioni**, l'intestazione del thread mostra lo stato: **Auto-risposta attiva** / **Risposta manuale**
/ **Risposta manuale (account)**.
- **Risposta manuale**: se rispondi dal **Composer**, la chat passa in **gestione umana** (`auto_reply=false`,
  `handoff_at` valorizzato). Banner: **"Stai gestendo tu questa chat"**.
- **Soft-pause**: il pulsante **"Pausa 2h"** mette l'AI in pausa **senza spegnere** l'auto-reply
  (`ai_disabled_until = now + 2h`) e poi **riprende da sola**; **"Riattiva AI"** la riattiva subito. Banner:
  **"AI in pausa · riprende tra …"**.
- API: `POST /conversations/{id}/ai-pause` (default **168h / 7 giorni**, range 1–720) · `…/ai-resume`.

**Verifica (Z)**: durante la pausa un inbound **non** riceve risposta del bot; `conversations.ai_disabled_until`
è valorizzato; scaduta la pausa (o premendo **"Riattiva AI"**) il bot torna a rispondere.

### 9.2 — Correzioni del bot (loop di addestramento dal Playground)
Nel **Playground**, dopo una risposta del bot premi **"Modifica"**, scrivi la versione **giusta** e
**"Salva correzione"** (badge **"corretta"**). La correzione (`trigger_message` + `original_response` +
`corrected_response`) viene confrontata con i messaggi **futuri** simili e iniettata nel system prompt come
**override obbligatorio**, **per merchant**.
- API: `POST/GET/PATCH/DELETE /catalog/{merchant_id}/corrections` (max **200** attive). Tabella `bot_corrections`.
- **Verifica (Z)**: la correzione compare nell'elenco; rimanda un messaggio simile al `trigger_message` → la
  nuova risposta segue la correzione. Disattivandola (`is_active=false`) **non** viene più applicata.

> Le correzioni sono parte della **lavorazione in corso** UC-08: il *matching* è euristico (sovrapposizione di
> parole) e si valuta a tempo di turno — vedi **§12 Limitazioni**.

---

## 10. Altri test (trasversali) — consigliati

### 6.1 — Isolamento multitenant (RLS)
Come **merchant_user** (con il JWT del merchant):
```bash
# Un altro merchant → 404 (la RLS nasconde la riga)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <merchant-jwt>" \
  https://api-production-6ac7.up.railway.app/merchants/<id-altro-merchant>      # → 404

# KPI di un altro merchant → 403 cross_merchant_kpis
curl -i -H "Authorization: Bearer <merchant-jwt>" \
  "https://api-production-6ac7.up.railway.app/analytics/merchant/kpis?merchant_id=<altro>"  # → 403
```
Come **agency_admin**:
- `GET /analytics/merchant/kpis` **senza** `merchant_id` → **403 `missing_merchant_id`**.
- `…?merchant_id=<merchant di un altro tenant>` → **404**.
- web-admin → **Inbox** vede le conversazioni di **tutti** i merchant; il merchant vede **solo le sue**.

### 6.2 — Firma webhook GHL
```bash
curl -i -X POST -H "x-ghl-signature: firma-finta" -H "Content-Type: application/json" \
  -d '{"type":"INSTALL","locationId":"x"}' \
  https://api-production-6ac7.up.railway.app/webhooks/ghl/marketplace   # → 401, nessuna scrittura
```

### 6.3 — Orari di attività (off-hours)
In **Configurazione** imposta `schedule.active_hours = "09:00-17:00"` e scrivi **fuori** da quell'orario →
il bot risponde con l'**off-hours message**: *"Grazie per averci contattato! Ti risponderemo al più presto."*
(invece della risposta normale). Ripristina `24/7` dopo.

### 6.4 — Escalation a operatore umano
Scrivi: **`Voglio fare un reclamo, chiamo l'avvocato`** (parole critiche: reclamo/avvocato/truffa/denuncia…).
**Atteso**: il bot risponde con tono di presa in carico ed emette `escalate_human`; la conversazione passa
in gestione umana (`auto_reply=false`). Verificalo nel Playground (evento `escalate_human`) o su una
conversazione reale.

### 6.5 — Finestra 24h
Prova a inviare una risposta **manuale** dal Composer (web-merchant → Conversazioni) **oltre 24h** dall'ultimo
messaggio in entrata del lead: il sistema la marca `failed` con motivo `outside_24h_window` e ti avvisa che
serve un template. Entro le 24h, invece, il testo libero parte.

### 6.6 — Persona / tono
In **Configurazione** imposta `bot.formality = "dai-del-lei"` → il bot inizia a **dare del Lei**. Con
`"dai-del-tu"` torna a dare del tu.

---

## 11. Checklist finale

| UC | Descrizione | Esito |
|----|-------------|:----:|
| UC-01 | First Response | ☐ |
| UC-02 | Booking | ☐ |
| UC-03 | Senza Risposta (chat + chiamata fallita) | ☐ |
| UC-04 | Spostamento Pipeline + Nota GHL | ☐ |
| UC-05 | Lead Scoring | ☐ |
| UC-06 | Riattivazione + Opt-out | ☐ |
| UC-07 | Knowledge Base + RAG | ☐ |
| UC-08 | Playground + Regole | ☐ |
| UC-09 | A/B Testing + significatività | ☐ |
| UC-10 | Template agenzia (default + lock) | ☐ |
| UC-11 | Dashboard merchant | ☐ |
| UC-12 | Dashboard agenzia + ranking | ☐ |
| UC-13 | Report obiezioni (merchant + agenzia) | ☐ |
| §6 | Lavagnetta: crea/attiva automazione · «Se (E/O)» · run su evento | ☐ |
| §7 | Agente: anti-falsa-conferma (tool-use) · fail-safe · staleness · consegna umana | ☐ |
| §8 | Template WhatsApp: validazione + ciclo Bozza→Approvato | ☐ |
| §9 | Handoff (pausa/takeover) · correzioni del bot | ☐ |
| Extra | RLS · firma webhook · off-hours · escalation · 24h · persona | ☐ |

---

## 12. Limitazioni note (da non scambiare per bug)

- **Export CSV (UC-12)**: backend pronto, **pulsante UI non ancora cablato**.
- **Lavagnetta — audit dei run (§6)**: **non esiste** una tabella `automation_runs`; l'esecuzione si verifica dai
  **log** (`automation.dispatch` / `automation.run`) e dagli **effetti** (tag, score, messaggi). Le automazioni
  personalizzate partono **solo se "Attive"** e con un ritardo fino a ~1 min (cron `automation_dispatch`); per i
  test immediati forza il dispatch a richiesta (Appendice B).
- **Intestazione immagine nei template (§8)**: `header_type=IMAGE` **non supportato in V1** (il validatore lo
  blocca). Solo intestazione testuale.
- **Correzioni del bot (§9.2)**: il *matching* sul messaggio del cliente è **euristico** (sovrapposizione di
  parole) e risolto a tempo di turno; è **lavoro in corso** — verificalo nel Playground con un messaggio molto
  simile al `trigger_message`.
- **Qualification**: non esiste un pulsante di qualificazione manuale; la qualifica è **guidata dal bot**
  (azione `move_pipeline` quando il lead supera la soglia, UC-04) e richiede GHL collegato.
- **Tool-use agente (§7.1)**: gli strumenti di lettura (`check_availability`/`lookup_appointment`) richiedono
  **GHL collegato**; la regola anti-falsa-conferma è nel prompt (compliance del modello), non un vincolo rigido —
  il loop tool-use **riduce** il rischio ancorando il bot prima della risposta.
- **Custom field GHL (UC-04)**: i dati raccolti viaggiano nella **nota** del contatto; la mappatura sui custom
  field GHL è rimandata (servono gli ID dei campi del tuo account).
- **Webhook dati GHL (UC-03 "chiamata fallita", sync contatti/opportunità)**: dipendono dalla *Default Webhook
  URL* dell'app GHL Marketplace (= `…/webhooks/ghl/marketplace`) e dalle sottoscrizioni eventi attive. Se non
  configurati, quel ramo si prova solo invocando l'handler dal worker (Appendice B).
- **Riattivazione / follow-up fuori 24h**: richiedono un **template WhatsApp approvato**; senza, il job
  **salta in modo pulito** (non è un errore).
- **Fine-tuning**: fuori dallo scope di questi test (fase separata).

---

## Appendice A — Query SQL di verifica (Supabase → SQL Editor)

> Sostituisci `<merchant_id>` / `<phone>` / `<conversation_id>` con i tuoi valori. Il telefono è in formato
> internazionale senza `+` (es. `393331234567`).

**A.1 — Conversazione e messaggi (UC-01)**
```sql
select count(*) from conversations where merchant_id = '<merchant_id>';
select role, direction, content, created_at
from messages
where conversation_id = '<conversation_id>'
order by created_at;
```

**A.3 — Appuntamenti & analytics booking (UC-02)**
```sql
select id, lead_id, ghl_appointment_id, calendar_id, start_at, status, source,
       meta->>'reminder_sent_at' as reminder_sent_at
from appointments
where merchant_id = '<merchant_id>'
order by start_at desc limit 5;

select event_type, variant_id, properties
from analytics_events
where merchant_id = '<merchant_id>' and event_type = 'booking.created'
order by occurred_at desc limit 5;
```

**A.4 — Lead/opportunità/pipeline (UC-04)**
```sql
select pipeline_stage_id, meta->>'ghl_opportunity_id' as opp_id,
       meta->>'ghl_pipeline_id' as pipeline_id
from leads where merchant_id = '<merchant_id>' and phone = '<phone>';

select event_type, variant_id, properties
from analytics_events
where merchant_id = '<merchant_id>' and event_type in ('pipeline.moved','pipeline.failed')
order by occurred_at desc limit 5;
```

**A.5 — Stato follow-up / origin chiamata (UC-03)**
```sql
select id, status, meta->>'origin' as origin, meta->>'reminders_sent' as reminders_sent,
       meta->>'last_reminder_at' as last_reminder_at, last_message_at, last_inbound_at
from conversations
where merchant_id = '<merchant_id>' and wa_contact_phone = '<phone>'
order by last_message_at desc;
```

**A.6 — Scoring (UC-05)**
```sql
select phone, name, email, score, score_reasons, meta->'content_signals' as content_signals
from leads where merchant_id = '<merchant_id>' order by updated_at desc limit 10;
```

**A.7 — Backdate per riattivazione + verifica (UC-06)**
```sql
-- rendi dormiente
update conversations set last_message_at = now() - interval '100 days'
where lead_id = (select id from leads where merchant_id='<merchant_id>' and phone='<phone>');
-- verifica esito
select meta->>'reactivation_attempts' as attempts, meta->>'last_reactivation_at' as last_at
from leads where merchant_id='<merchant_id>' and phone='<phone>';
```

**A.8 — Opt-out (UC-06)**
```sql
select phone, opted_out_at from leads where merchant_id='<merchant_id>' and phone='<phone>';
select event_type, properties from analytics_events
where event_type = 'lead.opted_out' order by occurred_at desc limit 5;
```

**A.9 — Knowledge base (UC-07)**
```sql
select id, title, source, status, status_detail, chunk_count, last_error
from knowledge_base_docs where merchant_id='<merchant_id>' order by created_at desc;
select doc_id, count(*) from kb_chunks where merchant_id='<merchant_id>' group by doc_id;
```

**A.10 — A/B stickiness (UC-09)**
```sql
select experiment_id, lead_id, variant_id, assigned_at
from ab_assignments order by assigned_at desc limit 20;  -- stesso lead → stessa variante
```

**A.11/A.12 — Conteggi dashboard**
```sql
select count(*) as lead_totali from leads where merchant_id='<merchant_id>';
select count(*) as booking from analytics_events
where merchant_id='<merchant_id>' and event_type='booking.created';
```

**A.13 — Obiezioni (UC-13)**
```sql
select category, severity, summary, quote, bot_variant, created_at
from objections where merchant_id='<merchant_id>' order by created_at desc limit 20;
```

---

**A.14 — Automazioni / Lavagnetta (§6)**
```sql
-- flussi del merchant (sistema + personalizzati)
select id, name, enabled, system_key, trigger_type
from automation_flows where merchant_id='<merchant_id>' order by system_key nulls last, name;

-- nodi e collegamenti di un'automazione
select node_key, kind, type, config from automation_nodes where automation_id='<automation_id>';
select source_key, target_key, branch from automation_edges where automation_id='<automation_id>';

-- evento che fa da trigger (per innescare un flusso a mano, vedi Appendice B)
select id, event_type, subject_type, subject_id, occurred_at
from analytics_events
where merchant_id='<merchant_id>' and event_type in
  ('message.received','booking.created','booking.failed','reminder.sent','lead_reactivation.sent')
order by occurred_at desc limit 10;
```

**A.15 — Handoff / pausa AI / correzioni (§9)**
```sql
-- stato handoff / soft-pause
select id, auto_reply, ai_disabled_until, handoff_at, handoff_reason, handoff_resolved_at
from conversations where merchant_id='<merchant_id>' and wa_contact_phone='<phone>'
order by last_message_at desc;

-- correzioni del bot (UC-08)
select trigger_message, original_response, corrected_response, is_active, created_at
from bot_corrections where merchant_id='<merchant_id>' order by created_at desc limit 20;
```

---

## Appendice B — Far partire i job a richiesta (Railway)

I cron girano dentro il **worker**. Per non aspettare la schedulazione, **accoda** il job: il worker in
esecuzione lo prende ed esegue con il contesto giusto.

```bash
railway run --service worker python -c "
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from shared import get_settings

JOB = 'followup_no_answer'   # oppure: reactivate_dormant_leads | send_appointment_reminders |
                             #          close_idle_conversations | daily_kpi_rollup
async def main():
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await pool.enqueue_job(JOB)
    print('accodato:', JOB)
asyncio.run(main())
"
```

**Simulare una "chiamata fallita" (UC-03b) senza webhook GHL** — accoda direttamente l'evento:
```bash
railway run --service worker python -c "
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from shared import get_settings
async def main():
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await pool.enqueue_job('handle_ghl_event', '<location_id>', 'OutboundCall',
        {'callStatus':'no answer','phone':'<phone>','contactId':'<ghl_contact_id>'})
    print('evento call_failed accodato')
asyncio.run(main())
"
```

**Far scattare subito un'automazione personalizzata (§6)** — forza il *tail* degli eventi senza aspettare il
cron (gira ogni minuto). Prima genera l'evento (es. scrivi su WhatsApp per `message.received`, oppure inserisci
una riga in `analytics_events`), poi:
```bash
railway run --service worker python -c "
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from shared import get_settings
async def main():
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await pool.enqueue_job('automation_dispatch')   # accoda gli automation_run delle automazioni Attive
    print('automation_dispatch accodato')
asyncio.run(main())
"
```

**Leggere i log** (utile quando un test non dà l'esito atteso):
```bash
railway logs --service worker
railway logs --service api
```

---

## Appendice C — Stringhe e default verbatim (riferimento rapido)

**Default di configurazione** (sovrascrivibili per merchant/agenzia):
`bot.auto_reply_enabled=false` · `scoring.hot_threshold=80` · `scoring.cold_threshold=30` ·
`pipeline.advance_threshold=60` · `no_answer.first_reminder_min=120` (min 30) · `second_reminder_min=1440` ·
`max_followups=2` · `reactivation.dormant_days=90` · `interval_days=7` · `max_attempts=3` ·
`booking.default_duration_min=30` · `booking.lookahead_days=14` · `rag.top_k=5` · `rag.min_score=0.7` ·
`schedule.active_hours="24/7"` · `schedule.timezone="Europe/Rome"` · `conversation.idle_close_minutes=120`.

**Default — agente & consegna (§7)** (sovrascrivibili per merchant/agenzia):
`agent.tool_use_enabled=true` · `agent.max_tool_iterations=3` (1–5) · `schedule.inbound_staleness_min=10` (0–1440,
0=off) · `delivery.debounce_window_s=8` (0–30) · `delivery.typing_indicator_enabled=true` ·
`delivery.typing_delay_base_s=1.0` · `delivery.typing_delay_min_s=1.0` · `delivery.typing_delay_max_s=6.0` ·
`delivery.typing_jitter_frac=0.25` · `delivery.multi_bubble_max=2` (1–4) · `delivery.bubble_max_chars=600` ·
`escalation.handoff_message=null` (vuoto = usa la frase di cortesia predefinita).

**Categorie obiezioni** (default): `prezzo, fiducia, tempistiche, concorrenza, necessita, altro`.

**Frasi-trigger tipiche** → azione del bot:
`vorrei prenotare giovedì alle 15` → `book_slot` · `quando siete liberi?` → `propose_slots` ·
`ok confermo, sono interessato` → `move_pipeline` · `voglio fare un reclamo / avvocato` → `escalate_human`.

**Testi automatici (verbatim):**
- Booking ok: `Perfetto, ho prenotato per te l'appuntamento del {data}. Ti invieremo il promemoria.`
- Proposta slot: `Ecco le prime disponibilità: …\nFammi sapere quale preferisci.`
- Slot occupato: `Quello slot non è più disponibile. Ti suggerisco: …\nFammi sapere quale preferisci.`
- Errore prenotazione: `Al momento non riesco a completare la prenotazione. Ti ricontatteremo a brevissimo.`
- Reminder appuntamento: `Promemoria: hai un appuntamento {data} alle {ora}. A presto!`
- Follow-up #1: `Ciao! Eri ancora interessato? Se vuoi posso aiutarti a completare la richiesta.`
- Follow-up #2: `Facciamo un ultimo tentativo — se vuoi riprendere la conversazione, rispondi pure.`
- Riattivazione #1: `Ciao! È passato un po' — se l'interesse è ancora vivo, possiamo riprendere da dove eravamo?`
- Riattivazione #2: `Un ultimo saluto: se vuoi che ti ricontattiamo, rispondi pure a questo messaggio.`
- Riattivazione #3: `Ci ripassi volentieri quando ti torna utile. A presto!`
- Off-hours: `Grazie per averci contattato! Ti risponderemo al più presto.`
- Nota GHL (UC-04): `[Reloop AI] Lead spostato in pipeline dalla conversazione WhatsApp.` + `Motivo:` / `Sentiment:` / `Nome:` / `Email:`.
- Fail-safe AI (§7.2): `Grazie per il tuo messaggio! Lo passo subito a un nostro operatore che ti risponderà a brevissimo.`
- Tool-use — slot libero (§7.1): `Lo slot richiesto (…) è LIBERO: puoi proporre di confermarlo.`
- Tool-use — slot occupato (§7.1): `Lo slot richiesto (…) NON è libero. Disponibilità reali più vicine: …`
- Tool-use — calendario assente (§7.1): `Calendario non collegato: non posso verificare le disponibilità reali.`
- Tool-use — nessun appuntamento (§7.1): `Il cliente non ha appuntamenti futuri registrati.`

**Parole di opt-out** (messaggio esatto): `stop, cancella, cancellami, annulla, disiscrivi, disiscrivimi, unsubscribe`.

**Etichette Lavagnetta (§6)** — Trigger: `Messaggio ricevuto` · `Nessuna risposta` · `Prenotazione creata` ·
`Prenotazione fallita` · `Lead dormiente`. Condizioni: `Temperatura lead` · `Punteggio lead` ·
`Finestra 24h aperta` · `Fascia oraria (UTC)` · `Messaggio contiene` · `Se (E / O)`. Azioni: `Invia messaggio` ·
`Risposta AI` · `Aggiorna lead/CRM` · `Passa a operatore` · `Attendi`. Badge: `Sistema` / `Attiva` / `Bozza`.
Flussi di sistema: `Nessuna risposta` · `Riattivazione dormienti` · `Promemoria appuntamento` · `Primo contatto`.

**Etichette Template WhatsApp (§8)**: stati `Bozza` / `In approvazione` / `Approvato` / `Rifiutato`; azioni
`Salva come bozza` / `Crea e invia per approvazione` / `Sincronizza stato` / `Modifica`.

**Etichette Handoff (§9)**: intestazione `Auto-risposta attiva` / `Risposta manuale` / `Risposta manuale (account)`;
banner `Stai gestendo tu questa chat` · `AI in pausa · riprende tra …` · pulsanti `Pausa 2h` / `Riattiva AI`;
Playground `Modifica` / `Salva correzione` / badge `corretta`.

---

## Appendice D — Dove guardare in GoHighLevel

| Cosa | Sezione GHL | UC |
|---|---|---|
| Contatto creato/aggiornato dal bot (nome, email, telefono) | **Contacts** → apri il contatto | UC-02, UC-04 |
| Nota interna `[Reloop AI] …` | **Contacts** → contatto → tab **Notes** | UC-04 |
| Opportunità e stage (new → qualified) | **Opportunities** → pipeline configurata | UC-02, UC-04 |
| Evento appuntamento | **Calendar** → calendario configurato | UC-02 |
