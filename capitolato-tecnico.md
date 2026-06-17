# ALLEGATO A — CAPITOLATO TECNICO

**Parte integrante e sostanziale del Contratto di Collaborazione**
**per la fornitura e lo sviluppo di Piattaforma SaaS con Agente AI**

tra

**BM ECOMMERCE LLC** (Fornitore)
e
**Wave Solutions SRL** (Cliente)

Sottoscritto in data 16/04/2026.

---

## 1. Descrizione del Progetto

Sviluppo della Versione 1 di una piattaforma SaaS con agente AI conversazionale per l'acquisizione e gestione automatizzata di lead, integrata con l'infrastruttura GoHighLevel (GHL) del Cliente e operante su WhatsApp.

La piattaforma adotta un'architettura multitenant a due livelli:

- **Tenant principale (agenzia / Cliente)** — dispone di un pannello admin con visibilità completa su tutti i merchant, dashboard unificata e gestione delle configurazioni default.
- **Sub-utenti (merchant)** — dispongono di un pannello dedicato con gestione bot, knowledge base, analytics e report specifici.

---

## 2. Team e Tempistiche

**Team dedicato:** 3 sviluppatori (2 Co-Founder + 1 Senior Developer)

**Durata complessiva:** 10 settimane (circa 2,5 mesi) dalla data di sottoscrizione del Contratto.

**Milestone:** 5 rilasci bisettimanali con deliverable verificabili.

Il progetto si articola in **tre fasi parzialmente sovrapposte**:

- **Sviluppo (settimane 1–8)** — costruzione completa della piattaforma e deploy in produzione.
- **Raccolta dati (settimane 7–10)** — 4 settimane di acquisizione di dati conversazionali reali dal flusso live del Cliente.
- **Fine-tuning (settimane 9–10)** — addestramento del modello AI sui pattern raccolti, test comparativi e rilascio finale.

---

## 3. Casi d'Uso — Versione 1

La Versione 1 comprende **13 casi d'uso** suddivisi in tre aree funzionali.

### 3.1 — Conversazione e Conversione (6 UC)

- **UC-01: First Response Istantaneo** — L'AI risponde su WhatsApp entro secondi con conversazione naturale, esegue qualificazione base.
- **UC-02: Booking Autonomo** — L'AI guida la lead verso la prenotazione in calendario, gestisce obiezioni leggere, invia conferma e reminder.
- **UC-03: Gestione Senza Risposta** — L'AI prende il controllo su WhatsApp quando la chiamata non va a buon fine, propone alternative.
- **UC-04: Spostamento Pipeline Automatizzato** — L'AI sposta le opportunità nella pipeline GHL e scrive note interne con sentiment e dati raccolti.
- **UC-05: Qualificazione Predittiva con Lead Scoring** — L'AI assegna un punteggio di qualità basato sulle risposte della lead.
- **UC-06: Riattivazione Database Dormiente** — L'AI avvia sequenze personalizzate verso contatti inattivi, filtra e riqualifica.

### 3.2 — Piattaforma e Configurazione (4 UC)

- **UC-07: Knowledge Base** — Interfaccia per caricare documenti, FAQ, tono di voce, istruzioni specifiche. Più configurazioni salvabili.
- **UC-08: Playground e Addestramento** — Ambiente di simulazione per testare il bot, aggiungere regole rigide e soft, salvare configurazioni.
- **UC-09: A/B Testing Bot** — Split percentuale del flusso lead tra configurazioni diverse, metriche tracciate separatamente.
- **UC-10: Bot Default Agenzia** — L'agenzia crea configurazioni default visibili come template per i merchant.

### 3.3 — Analytics e Reporting (3 UC)

- **UC-11: Dashboard Analytics Merchant** — KPI chiave: lead ricevute, tasso di risposta, booking rate, scoring distribution, filtri per periodo e campagna.
- **UC-12: Dashboard Unificata Admin Agenzia** — Dashboard aggregata su tutti i merchant, ranking, drill-down per singolo merchant.
- **UC-13: Report Obiezioni e Insight** — Mappatura e categorizzazione obiezioni, report on-demand con trend temporali, filtri per bot e merchant.

---

## 4. Piano Milestone e Allocazione Costi

| Milestone | Settimane | Deliverable | Allocazione |
|---|---|---|---|
| **M1** | Sett. 1–2 | Architettura multitenant, auth, setup DB, integrazione API GHL + UC-01: First Response | € 3.600 |
| **M2** | Sett. 3–4 | UC-02: Booking + UC-03: Senza Risposta + UC-04: Pipeline + UC-05: Lead Scoring | € 4.200 |
| **M3** | Sett. 5–6 | UC-06: Riattivazione + UC-07: KB + UC-08: Playground + UC-09: A/B Testing + UC-10: Bot Default | € 3.800 |
| **M4** | Sett. 7–8 | UC-11: Dashboard Merchant + UC-12: Dashboard Admin + UC-13: Report Obiezioni + deploy produzione | € 3.200 |
| **M5** | Sett. 9–10 | Raccolta dati, fine-tuning modello AI, test comparativi, rilascio finale, onboarding | € 3.200 |
| | | **TOTALE** | **€ 18.000** |

---

## 5. Cosa è Incluso

- Sviluppo completo della Versione 1 (13 casi d'uso)
- Architettura multitenant (pannello admin agenzia + pannelli merchant)
- Integrazione completa con GoHighLevel (API pipeline, calendario, custom fields)
- Agente AI conversazionale su WhatsApp
- Knowledge Base, Playground, A/B Testing
- Dashboard analytics (merchant + admin) e Report Obiezioni
- 4 settimane di raccolta dati e fine-tuning del modello AI su dati conversazionali reali
- Onboarding assistito al go-live
- 30 giorni di supporto post-lancio per bug fixing

---

## 6. Cosa NON è Incluso

- Costi di infrastruttura e hosting (server, API AI, WhatsApp Business API)
- Whitelabel, sistema referral, supporto multicanale (roadmap futura)
- Manutenzione evolutiva post-30 giorni
- Creazione contenuti per la knowledge base (a carico del Cliente)
- Campagne marketing, ads, creatività

---

## 7. Obblighi del Cliente

Per la corretta esecuzione del progetto, il Cliente si impegna a:

- Fornire accesso completo all'account GoHighLevel e alle relative API
- Fornire le credenziali WhatsApp Business API
- Garantire accesso agli storici delle conversazioni per la fase di fine-tuning
- Garantire un volume minimo di lead in ingresso durante le settimane 7–10 per la raccolta dati
- Rispondere alle richieste di chiarimento entro tempi ragionevoli
- Rispettare le scadenze di pagamento

---

Il presente Allegato A costituisce parte integrante e sostanziale del Contratto di Collaborazione.

**Luogo e data:** 16/04/2026

**Il Fornitore**
BM ECOMMERCE LLC

Firma: ___________________________

**Il Cliente**
Wave Solutions SRL

Firma: ___________________________
