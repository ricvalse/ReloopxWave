# ADR 0007 — GoHighLevel marketplace agency-install (multi-sub-account)

Status: Accepted (2026-06-15)

## Context

The first GHL integration was **single-location and merchant-initiated**: each merchant clicked "Connetti GHL" in their portal, ran an OAuth round-trip with `user_type="Location"` hardcoded, and the location token landed in `integrations(merchant_id, provider='ghl')`. The unique constraint `(merchant_id, provider)` allowed exactly one GHL row per merchant, the signed OAuth state carried only a `merchant_id`, and the GHL webhook lived at `POST /webhooks/ghl/{merchant_id}` (merchant id baked into the URL).

That model does not match how an agency actually adopts the product. An agency owns many sub-accounts and wants to enable the AI agent across them in one motion, from a single GHL Marketplace app that the agency (not we) installs. The real GHL marketplace mechanics (verified against the 2026 docs):

- The agency completes **one** OAuth round-trip → exchange the code with `user_type="Company"` → an **Agency token** scoped to a `companyId` (24h, refresh rotates).
- GHL then fires an **INSTALL webhook per selected sub-account** to the app's Default Webhook URL: `{type:"INSTALL", installType:"Location", locationId, companyId, userId, companyName}` (and `UNINSTALL` symmetrically). These are signed with GHL's **RSA public key**, not the HMAC used for data webhooks.
- A **Location-level token** is minted on demand from the Agency token: `POST /oauth/locationToken` with `companyId`+`locationId`.

## Decision

Move GHL to the **agency-install marketplace model**, and make it the *only* GHL connection path (the per-merchant self-service flow is removed). Three product decisions, confirmed with the product owner:

1. **GHL = CRM/calendar only.** The messaging channel stays 360dialog/WhatsApp (inbound still resolves by `phone_number_id`); GHL is used for contacts, opportunities and calendar booking. The conversation pipeline is untouched.
2. **Link to existing merchants** (no auto-provisioning). An INSTALL records a `pending_link` location; an agency admin links it to an already-onboarded merchant from web-admin.
3. **Agency-managed only.** The per-merchant `POST /integrations/ghl/oauth/start` + `user_type="Location"` flow and the merchant-portal "Connetti GHL" button are removed.

Schema — two new tables (two cardinalities, see why below):

- `ghl_agency_installs` — one Agency token per tenant, plus the `company_id → tenant` mapping. Tenant-scoped RLS.
- `ghl_location_tokens` — one Location token per installed `locationId`, with `merchant_id` **nullable** (a location can exist before it is linked) and `tenant_id` **denormalised**. RLS mirrors the `users` tenant-OR-merchant predicate; `location_id` is globally unique for O(1) webhook lookup and INSTALL idempotency.

Flow at runtime:

1. Agency admin clicks "Collega Agenzia GHL" in web-admin → `POST /integrations/ghl/agency/oauth/start` signs a state with `tenant_id` → GHL authorize URL.
2. `GET /integrations/crm/oauth/callback` exchanges the code with `user_type="Company"`, stores `ghl_agency_installs`, redirects back to web-admin.
3. `POST /webhooks/ghl/marketplace` validates the RSA signature and enqueues `handle_ghl_install` / `handle_ghl_uninstall`. The install worker resolves the tenant from `companyId`, records the `pending_link` location, mints the location token (refreshing the agency token once on rejection), and fetches the location name (best-effort) for the linking UI.
4. The admin links each location to a merchant (`POST /integrations/ghl/locations/{id}/link`); booking then resolves the location token by `merchant_id` exactly as before (`IntegrationRepository.resolve_ghl` now reads `ghl_location_tokens`).

## Rejected alternatives

- **Reuse the `integrations` table for GHL location/agency tokens.** `integrations.merchant_id` is `NOT NULL` with a merchant FK and merchant-scoped RLS; an agency token has no merchant, and a location can be installed before it has a merchant. Relaxing the column to nullable would make those rows invisible to every JWT path and pollute a table shared with WhatsApp. Dedicated tables keep each credential at its correct tenancy level and reuse the two existing RLS patterns verbatim.
- **Auto-provision a merchant per INSTALL.** A Reloop merchant carries invariants (slug, bot config, 360dialog channel, timezone). Auto-creating one per location yields orphan merchants with no channel/config. Link-to-existing keeps the merchant lifecycle clean; auto-provisioning can be added later behind a per-tenant flag.
- **Make GHL the messaging channel (Conversation Provider + InboundMessage webhook).** This would rewrite half the conversation pipeline (`handle_inbound`, `ReplySender`, `Conversation` model, status callbacks) for no near-term gain — 360dialog already owns the WhatsApp transport. The location-resolver (`resolve_ghl_by_location_id` shape) is left in place so this can be added as an optional channel later without redoing the install/token work.
- **N OAuth callbacks (one per sub-account).** The agency's mental model ("10 callbacks") is wrong: there is one OAuth redirect (Company token) and N INSTALL webhooks. Building around per-location redirects would not match GHL.

## Consequences

Positive:
- An agency connects once and enables N sub-accounts via the Marketplace install link; we keep the Client ID/Secret on our developer account.
- Per-location tokens give per-merchant blast radius; the Agency token can re-mint any location token if its refresh token is lost.
- `integrations` stays clean (WhatsApp only); the two GHL tables map 1:1 to their tenancy level.

Negative / watch:
- **INSTALL before agency-connect**: if a `companyId` has no agency install yet, the INSTALL is logged and dropped (200, no retry storm). The agency must connect from web-admin before installing; a later re-install recovers. A backfill job (list locations via API + re-mint) is a possible follow-up.
- **Marketplace signature**: the exact header name (`x-wh-signature`) and RSA algorithm should be reconfirmed against GHL before go-live; the verifier takes the PEM from `GHL_MARKETPLACE_PUBLIC_KEY` so it can be rotated/adjusted without a code change.
- **Refresh race**: two concurrent turns for one location could both refresh and invalidate each other's refresh token. V1 self-heals via re-mint from the agency token; a Redis lock on `ghl:refresh:{location_id}` is a V1.5 hardening.
- Legacy `integrations(provider='ghl')` rows (if any in prod) become inert; the new resolver does not read them. Clean up if present.

## Revisit if

- We want GHL as an inbound/outbound channel (SMS or GHL conversations) — wire `resolve_ghl_by_location_id` into a new inbound entrypoint + a GHL `ReplySender`.
- Auto-provisioning is requested for high-volume agencies — add a per-tenant flag that creates a merchant on INSTALL from `GET /locations/{id}`.
- Refresh-race contention shows up in logs — add the Redis lock.

## Where the work landed

Backend:
- `backend/libs/db/src/db/models/ghl.py`, `repositories/ghl_marketplace.py`, `migrations/versions/0015_ghl_marketplace.py` (new); `repositories/integration.py` (`resolve_ghl` reads `ghl_location_tokens`; `upsert_ghl` removed).
- `backend/libs/integrations/src/integrations/ghl/oauth.py` (state→tenant_id, `exchange_authorization_code(user_type=...)`, `mint_location_token`), `client.py` (`GHLTokenBundle.company_id/user_type`, `refresh_now`, `get_location`), `marketplace_signatures.py` (new RSA verifier).
- `backend/services/api/src/api/routers/integrations.py` (agency start/callback/status/locations/link), `routers/webhooks.py` (`POST /webhooks/ghl/marketplace`).
- `backend/workers/conversation/handlers.py` (`handle_ghl_install`/`handle_ghl_uninstall` + token helpers), `workers/settings.py` (registration); `ai_core/actions/{booking,pipeline}.py` (persist rotated token to `ghl_location_tokens`).
- `backend/libs/shared/src/shared/settings.py` (`ghl_marketplace_public_key`).

Frontend:
- `frontend/apps/web-admin/src/app/(app)/integrations/page.tsx`, `components/integrations/{agency-ghl-panel,installed-locations-list}.tsx` (new), `config/nav.ts` (Integrazioni).
- `frontend/apps/web-merchant/src/components/integrations/integrations-panel.tsx` (GHL card now read-only / agency-managed).

Tests:
- `backend/tests/unit/{test_ghl_oauth_state,test_integrations_oauth,test_ghl_marketplace_signature,test_ghl_location_token,test_ghl_install}.py`.
- `backend/tests/integration/test_isolation_ghl_marketplace.py` (RLS).

Operations (GHL Developer Portal):
- App: Target User = Sub-Account, Distribution = "Both Agency and Sub-account".
- Redirect URI = `${PUBLIC_API_BASE_URL}/integrations/crm/oauth/callback`.
- Default Webhook URL = `${PUBLIC_API_BASE_URL}/webhooks/ghl/marketplace`.
- Set `GHL_MARKETPLACE_PUBLIC_KEY` (PEM) for INSTALL/UNINSTALL signature verification.
