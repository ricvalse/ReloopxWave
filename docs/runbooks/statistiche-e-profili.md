# Runbook — Statistiche configurabili, esiti e profili di conversazione

Come si usa quello che ADR 0023 (attribuzione + esiti) e ADR 0022 (profili) hanno
introdotto: far vedere a un merchant quanti messaggi ha inviato, a quanti è stato
risposto e quanti hanno raggiunto un esito che decide lui, con la pagina divisa
per profili di conversazione.

Legenda: 🔴 bloccante · 🟡 consigliato · ⚪️ opzionale.

---

## 0. Prerequisiti

1. 🔴 **Deploy dell'API.** La migrazione `0047` si applica **da sola**: l'entrypoint
   fa `alembic upgrade head` a ogni boot con advisory lock
   (`infra/docker/api-entrypoint.sh`). Non serve lanciarla a mano — a meno che
   il servizio abbia `RUN_MIGRATIONS=0`, nel qual caso:
   `railway run --service api alembic upgrade head`.
2. 🔴 **Deploy del worker.** I nodi nuovi (`emit_outcome`,
   `set_conversation_profile`) e i cancelli girano lì. Un'automazione salvata ma
   con il worker vecchio non registra niente e non logga errori evidenti.
3. 🟡 **Verifica**: `GET /statistics/outcomes` deve rispondere `200 []` invece di
   `404`. Se dà 404, l'API in produzione è ancora quella vecchia.

---

## 1. Le bolle automatiche — zero configurazione

Messaggi inviati, risposte ricevute, persone raggiunte e tempo di risposta
**funzionano da subito**, senza dichiarare niente e senza toccare le automazioni:
escono dalle colonne che il sistema scrive comunque a ogni invio.

**Statistiche → Bolle → sezione «Automatiche» → clic sul preset.** Poi *Salva*.

⚠️ I numeri partono **dal deploy in avanti**. Lo storico precedente non è
attribuibile: quei messaggi non portavano l'informazione da nessuna parte, e la
migrazione non può inventarla. Nella prima settimana i conteggi sembreranno
bassi — è atteso.

---

## 2. Una statistica personalizzata

Tre passaggi, in quest'ordine. Il secondo è quello che si dimentica.

### 2.1 Dichiarala

**Statistiche → Statistiche personalizzate → compila e crea.**

| campo | cosa mettere |
|---|---|
| Nome | quello che vedrai sulla bolla. Rinominabile a piacere, non perde lo storico. |
| Identificativo | stabile: **non si cambia più**. Ci puntano le righe raccolte. |
| Come viene accertato | webhook (fatto certo) · AI (dichiarazione dedotta) · operatore (verificata da umano) |
| Quante volte può accadere | `once_per_lead` è il caso normale · **non modificabile dopo** |

> Perché la cardinalità è immutabile: è denormalizzata su ogni riga raccolta e
> governa quale indice unique si applica. Cambiarla lascerebbe le righe vecchie
> sotto un vincolo e le nuove sotto un altro, cioè conteggi incoerenti. Se serve
> cambiarla, si crea una statistica nuova.

### 2.2 Cablala in un'automazione

**Automazioni → il flusso della campagna.** Il nodo che registra va **in fondo**,
dopo i cancelli:

```
[trigger: Messaggio ricevuto]        trigger_config → profilo della campagna
        ↓
[Ha già l'esito …?]        ──sì──▶ (fine)
        ↓ no
[Sta rispondendo a → <nodo che ha fatto la domanda>]   ──no──▶ (fine)
        ↓ sì
[Condizione AI: "il lead conferma di …?"]   ──no──▶ (fine)
        ↓ sì
[Registra esito → <la tua statistica>]
```

🔴 **L'ordine dei cancelli non è estetica, è la voce di costo.** Un flusso con una
condizione AI su «Messaggio ricevuto» **senza** cancelli davanti fa una chiamata
al modello per **ogni messaggio in ingresso del merchant**, non solo per quelli
della campagna. Con i tre cancelli il modello viene interpellato solo su chi sta
davvero rispondendo a quella domanda e non ha già l'esito.

- «Ha già l'esito» va usato **negato** (ramo `no` prosegue): chi ha confermato
  esce definitivamente dal perimetro.
- «Sta rispondendo a» è preferibile a «Messaggio contiene»: se la domanda era
  *"hai compilato il questionario?"*, il lead risponde **"sì"** e nessuna parola
  chiave corrisponderebbe.
- Il filtro sul **trigger** (`profilo`) è più efficiente della condizione
  equivalente nel grafo: scarta prima di accodare il job, invece di accodarlo,
  costruire il contesto AI e poi buttarlo.

### 2.3 Mostrala

**Statistiche → Bolle → sezione «Personalizzate» → clic sulla statistica → Salva.**

La bolla mostra *"312, di cui 180 verificati"* quando parte del numero viene da
una sorgente certa (webhook / marcatura umana) e parte è dedotta: un numero
inferito non va presentato come un fatto.

---

## 3. Profili di conversazione

Servono quando lo stesso merchant deve comportarsi in modi diversi — es. un
parrucchiere con «Reception» di default e «Colloqui HR» caricato da una campagna.

### 3.1 Creali

**Statistiche → Profili → crea.** Il primo profilo creato diventa
automaticamente il default.

### 3.2 Dai le istruzioni

**Sul profilo → «Istruzioni».** Ogni campo porta un badge:

- **Ereditato** — vale quello del merchant, mostrato in chiaro sotto al campo.
- **Personalizzato** — il profilo lo sovrascrive. «Torna a ereditato» rimuove la
  chiave (≠ scriverci sopra una stringa vuota).

Un profilo è un **delta**: informazioni aziendali, knowledge base, orari e
prenotazioni restano del merchant. Cambia *come parla* il bot, non su quale
calendario prenota. Per un profilo non commerciale metti *Modalità → «Solo
direttive»* e lascia fra le azioni permesse solo «Passa a operatore».

### 3.3 Caricalo da un'automazione

**Automazioni → nodo «Carica profilo»**, di norma **subito dopo il trigger**.

🔴 **Attenzione alla tempistica.** Con trigger «Messaggio ricevuto» il profilo
arriva *dopo* la prima risposta:

```
t=0     il lead scrive
t≈8s    il bot risponde   ← ancora con il profilo precedente (debounce 8s)
t≤60s   il dispatcher fa partire l'automazione → carica il nuovo profilo
poi     dal messaggio successivo in avanti vale il nuovo profilo
```

Per avere il profilo giusto **dal primo messaggio**, scegli una di queste:

| se vuoi… | fai così |
|---|---|
| profilo attivo prima che il lead scriva | trigger **«Nuovo lead dal CRM»** / «Opportunità creata» |
| che risponda l'automazione, non il bot | «Carica profilo» → «Risposta AI» **nello stesso flusso** (stessa passata) |
| che il bot non risponda mai da solo | lascia `auto_reply` spento sul merchant |

Il profilo resta sulla conversazione **fino alla chiusura per inattività**
(`close_idle_conversations`), poi si torna al default. Non è stato di run: è una
colonna su `conversations`.

### 3.4 Bolle diverse per profilo

Con un profilo selezionato nel menu in alto, il metric-builder configura le bolle
**di quel profilo**. Un profilo senza bolle proprie eredita quelle del merchant —
il badge in alto lo dice.

---

## 4. Quando un numero non torna

| sintomo | causa probabile |
|---|---|
| tutte le bolle a zero | migrazione non applicata (API vecchia) oppure i dati sono precedenti al deploy |
| «messaggi inviati» a zero ma i messaggi partono | l'invio non passa da un'automazione (es. composer, o scheduler senza automazione): `automation_id` è NULL |
| «risposte ricevute» molto basse | è **last-touch e solo la prima**: se il lead scrive tre volte di fila conta una risposta sola. È voluto — altrimenti il tasso supererebbe il 100% |
| statistica personalizzata a zero | il flusso non arriva mai al nodo. Controlla in ordine: il trigger scatta? i cancelli passano? `auto_reply` del merchant è acceso (default **False**)? |
| l'esito viene contato una volta sola | corretto con `once_per_lead`: l'indice unique rende la seconda registrazione un no-op. Il motore ri-esegue il ramo a ogni messaggio successivo |
| il profilo non ha effetto sulla prima risposta | vedi §3.3 |
| il profilo non ha effetto **mai** | il campo è rimasto «Ereditato»: un badge grigio significa che quel knob non è sovrascritto |

**Log utili** (structlog → Railway):
`automation.emit_outcome` (con `created=true|false`),
`automation.set_conversation_profile`, `automation.has_outcome.skipped`,
`automation.ai_check.skipped`.

---

## 5. Limiti noti

- **`source` sempre `'automation'`** per gli esiti registrati dalla lavagnetta,
  qualunque cancello ci sia a monte. Il conteggio "di cui N verificati" resta
  corretto (un esito dedotto non viene mai spacciato per certo), ma non
  distingue un cancello a parole chiave da uno AI.
- **Nessuna azione dell'agente**: se il bot conversa, l'esito potrebbe essere
  registrato dall'agente stesso a costo zero. Oggi esiste solo il percorso
  lavagnetta, che costa una chiamata LLM in più.
- **Storico non attribuibile**: `automation_id` è NULL sui messaggi precedenti
  alla migrazione.
- **Eliminare una statistica cancella i dati raccolti** (CASCADE). Per smettere
  di misurare conservando lo storico si usa «Disattiva».
