# Runbook — Re-autenticazione GoHighLevel (token scaduto/revocato)

Cosa fare quando il token OAuth di una location GHL è scaduto, revocato o non più valido (booking/CRM smettono di funzionare per un merchant).

## Contesto (ADR 0007)

GHL è una **marketplace agency-install app**: l'agenzia (= tenant) si collega **una volta** da web-admin, le location arrivano come webhook `INSTALL` su `POST /webhooks/ghl/marketplace` (firma a chiave pubblica globale Ed25519/RSA) e vengono mintate in `ghl_location_tokens` (per-`locationId`), poi linkate ai merchant dalla UI admin. **Non esiste** un flusso GHL self-service per-merchant.

- I token vivono in `ghl_agency_installs` (token Agency/Company) e `ghl_location_tokens` (per location), **non** nella tabella `integrations` (che è solo WhatsApp).
- `IntegrationRepository.resolve_ghl(merchant_id)` legge il token della location linkata.
- GHL **ruota il `refresh_token` ad ogni refresh**: il client lo ri-espone perché venga ripersistito (`ghl/client.py:53`). Se la rotazione non viene salvata, il refresh successivo fallisce con `invalid_grant`.

## Sintomi

- Booking via messaggio non crea l'evento; pipeline/contatti non si aggiornano.
- Log con `invalid_grant` / 401 dalle chiamate GHL.
- Il merchant compare come integrazione GHL non sana (post fix health-check #24 itera `list_active_linked_locations()` con liveness call).

## Diagnosi

1. Identifica `merchant_id` e la `locationId` linkata.
2. Verifica nei log se l'errore è:
   - **refresh_token rotato non persistito** → un solo refresh fallisce, poi serve re-auth;
   - **revoca lato GHL** (app disinstallata dalla location → webhook `UNINSTALL`) → il token è stato rimosso/invalidato e serve re-install.

## Risoluzione

### Caso A — token scaduto, install ancora attiva
1. Verifica che l'**Agency install** sia valida: `ghl_agency_installs` ha un refresh_token funzionante.
2. Forza un refresh della location: il client (`ghl/client.py`) refresha automaticamente al primo uso; assicurati che il **refresh_token rotato venga persistito** su tutti i call-site (gap noto risolto, audit §5). Se vedi `invalid_grant` ripetuto, il refresh_token salvato è stale → procedi con la re-install (Caso B).

### Caso B — install revocata / refresh_token bruciato → re-install
1. L'agency_admin re-installa l'app GHL sulla location dal Marketplace GHL (oppure il merchant la re-installa dalla sua location).
2. Arriva un nuovo webhook `INSTALL` su `POST /webhooks/ghl/marketplace` → viene mintato un nuovo `ghl_location_tokens`.
3. Dall'admin UI, **ri-linka** la location al merchant (se il link non è già presente).
4. Verifica: una chiamata di booking/CRM di prova per quel merchant.

### Caso C — Agency token (Company) compromesso
1. Re-esegui l'OAuth agency da web-admin: `POST /integrations/ghl/agency/oauth/start` (`user_type="Company"`), completa il callback su `/integrations/crm/oauth/callback`.
2. Le location esistenti restano; se necessario re-installa le singole location (Caso B).

## Note di sicurezza
- I redirect URI usano il path `crm`, NON `ghl` (GHL rifiuta URI col brand HighLevel).
- Le credenziali sono cifrate at-rest (AES-256-GCM, KEK env); non loggarle in chiaro.
- Eventi marketplace non firmati o con firma non valida vengono scartati (Ed25519 preferito, RSA legacy deprecato 2026-07-01).

## Riferimenti
- ADR `docs/decisions/0007-ghl-marketplace-agency-install.md`
- `backend/libs/integrations/src/integrations/ghl/{client,oauth,marketplace_signatures}.py`
- `backend/services/api/src/api/routers/integrations.py`, `routers/webhooks.py`
- Health-check: `backend/workers/scheduler/integration_health.py`
