# Runbook — Slack App "Add to Slack" (notifica handoff)

Setup **una tantum** di una singola Slack App di Reloop che ogni merchant
installa nel proprio workspace con un click (OAuth v2, scope `incoming-webhook`).
Vedi ADR 0020. Dopo questo setup il merchant fa solo: *Integrazioni → Aggiungi a
Slack → sceglie il canale → Consenti*, e la notifica handoff è già attiva
(automazione seedata in automatico).

## 1. Crea l'app Slack

1. Vai su https://api.slack.com/apps → **Create New App** → *From scratch*.
2. Nome (es. "Reloop") + il workspace di sviluppo → **Create App**.

## 2. OAuth & Permissions

1. Menu laterale **OAuth & Permissions**.
2. **Scopes → Bot Token Scopes** → *Add an OAuth Scope* → aggiungi **`incoming-webhook`**
   (è l'unico scope necessario).
3. **Redirect URLs** → *Add New Redirect URL* → incolla:
   ```
   https://<PUBLIC_API_BASE_URL>/integrations/slack/oauth/callback
   ```
   (es. `https://api.wave.relooptech.ai/integrations/slack/oauth/callback`) → **Save URLs**.
   Deve combaciare **esattamente** con `SLACK_REDIRECT_URI` (o con quello derivato
   da `PUBLIC_API_BASE_URL`), altrimenti Slack rifiuta lo scambio.

## 3. Credenziali → env

Da **Basic Information → App Credentials** copia *Client ID* e *Client Secret* e
impostali sul servizio **API** (e worker, se condividono il var group) su Railway:

| Env | Valore | Note |
|-----|--------|------|
| `SLACK_CLIENT_ID` | Client ID | obbligatorio |
| `SLACK_CLIENT_SECRET` | Client Secret | obbligatorio, segreto |
| `SLACK_REDIRECT_URI` | l'URL del punto 2.3 | opzionale; se vuoto si deriva da `PUBLIC_API_BASE_URL` |
| `SLACK_OAUTH_STATE_SECRET` | stringa random | opzionale; se vuoto usa `SLACK_CLIENT_SECRET` per firmare lo `state` |
| `HANDOFF_SLA_MINUTES` | es. `15` | opzionale, default 15 — soglia dell'alert "handoff in ritardo" |

## 4. Distribuzione (multi-workspace)

Perché l'app sia installabile da workspace diversi dal tuo:
1. Menu **Manage Distribution** → completa la checklist (redirect URL, niente
   secret hardcoded nel client, ecc.) → **Activate Public Distribution**.
2. Non serve pubblicarla sull'App Directory: il flusso OAuth funziona con l'app
   in "public distribution". Se vuoi il pulsante ufficiale, l'endpoint
   `/integrations/slack/oauth/start` già assembla l'authorize URL — il FE lo usa.

## 5. Verifica

1. Merchant portal → **Integrazioni** → card Slack → **Aggiungi a Slack**.
2. Slack chiede il canale + *Consenti* → torni sul portal con «Slack connesso».
3. Controlla nei log API: `integrations.slack.oauth.completed` +
   `integrations.slack.automation_seeded` (alla prima connessione).
4. Forza un handoff (scrivi "voglio un operatore" al numero WhatsApp del merchant)
   → entro ~60s arriva la notifica nel canale scelto.

## Rotazione del secret

Rigenera il *Client Secret* da **Basic Information → App Credentials → Regenerate**,
aggiorna `SLACK_CLIENT_SECRET` su Railway, redeploy. I webhook già emessi restano
validi (sono indipendenti dal client secret); si rompe solo lo scambio OAuth di
nuove installazioni finché l'env non è aggiornato. Se `SLACK_OAUTH_STATE_SECRET`
è lasciato derivare dal client secret, gli `state` in volo (max 10 min) diventano
invalidi alla rotazione — impostane uno dedicato per disaccoppiarli.
