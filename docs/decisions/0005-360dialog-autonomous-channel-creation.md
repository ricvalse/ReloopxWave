# ADR 0005 — Autonomous 360dialog channel creation per merchant

Status: Accepted (2026-04-28)

## Context

Until commit `8e8165e` (April 2026) every merchant's WhatsApp number had to be onboarded by hand: an operator with Partner Hub access provisioned a channel inside 360dialog, copied the resulting `phone_number_id`, and pasted it into the merchant portal at `/integrations`. The portal called `POST /integrations/whatsapp/verify` which probed the platform-level Partner key against that channel and stored the `phone_number_id` in `integrations.meta`. No per-merchant credential lived in the DB — every send used the single platform-level `WHATSAPP_PARTNER_API_KEY`.

That was acceptable while we onboarded a handful of merchants by ourselves. The product direction now is self-serve: a merchant should be able to sign up, click one button, and get their own WhatsApp number live without a human at Wave Marketing in the loop.

A sibling project (`/Users/riccardo/Progetti/Amalia/amalia-ai`) already runs this flow against the same 360dialog Partner. Porting that flow rather than designing from scratch saves time and avoids re-discovering the wire-shape quirks 360dialog has accumulated.

## Decision

Adopt the same **Embedded Signup popup + Partner Hub credential exchange** that amalia-ai uses. Concretely:

1. The merchant portal exposes a "Collega WhatsApp" button. Clicking it opens `https://hub.360dialog.com/dashboard/app/{partner_id}/permissions?store_id={merchant_id}` in a popup.
2. 360dialog hosts the Meta Business signup. The merchant authenticates with Facebook, picks (or ports) a phone number, and verifies it. We are not in this flow.
3. 360dialog redirects (URL pre-configured in the Partner Hub admin) to `${PUBLIC_WEB_MERCHANT_URL}/integrations?client=<phone>&channels=[<channel_id>]`. A `useEffect` on the integrations page reads those params and POSTs them to a new route, `POST /integrations/whatsapp/channels`.
4. The route does three Partner-Hub / WABA calls in sequence:
   - `D360PartnerClient.generate_channel_api_key(channel_id)` → mints a per-channel `D360-API-Key`.
   - `D360WhatsAppClient.fetch_phone_number_id()` → resolves Meta's `phone_number_id` (distinct from 360dialog's `channel_id`).
   - `D360WhatsAppClient.configure_webhook(url)` → registers `${PUBLIC_API_BASE_URL}/webhooks/whatsapp/{phone_number_id}` as the inbound webhook.
5. The encrypted per-channel key, plus `phone_number_id`, `channel_id`, and `waba_id`, are persisted via `IntegrationRepository.upsert_whatsapp(...)`. Outbound sends use the per-channel key directly — the platform key is now reserved for Partner Hub admin calls only.

## Rejected alternatives

- **Server-side-only channel creation**: 360dialog's Partner API does not let us create production channels without the Meta Embedded Signup flow. We could create *sandbox* channels server-side, but they cannot send real WhatsApp messages to consumer phones. Not viable for production.
- **Custom Meta Business signup UI inside our app**: Meta does not provide a public API for the consent + number-verification dance. Embedded Signup *is* the API. Building a custom UI would require us to become a Meta tech provider, which is months of paperwork and not on the roadmap.
- **Polling for channel status via a worker**: amalia-ai also has a partner-callback webhook (`/webhooks/360dialog/callback`) that receives `channel.created/updated/deleted` events from 360dialog. We deliberately defer that to a follow-up because the synchronous credential exchange in step 4 is sufficient for the initial connection — the partner callback is mostly useful for detecting *later* lifecycle changes (suspensions, deletions). Adding it doubles the surface we have to verify against 360dialog without unblocking onboarding.

## Consequences

Positive:
- Merchants can onboard themselves end-to-end without an operator at Wave Marketing.
- Per-channel keys give us per-merchant blast radius: rotating one merchant's key doesn't disturb the others.
- The Partner key is now used only for the Partner Hub admin API surface. If 360dialog rotates it we re-onboard once, not every merchant.
- The integrations row's `secret_ciphertext` finally holds something meaningful (was a fixed placeholder string for the manual flow).

Negative / watch:
- The redirect URL in 360dialog Partner Hub is global per-Partner — staging and production must either share a Partner with a redirect that handles both via a hosted state file, or have separate Partners. We currently run with a single Partner; staging will need its own once it lands.
- Webhook URL registration runs at onboarding time and requires `PUBLIC_API_BASE_URL` to be set. If that env var is wrong we silently configure a bad URL on 360dialog's side. Mitigated by the route raising `IntegrationError` early if `PUBLIC_API_BASE_URL` is empty, but we cannot detect a *wrong* URL until inbound messages fail to arrive.
- Embedded Signup is opaque — if it fails the merchant lands back on `/integrations` with no `channels` param, and we cannot tell why. Mitigated by leaving the manual paste flow available behind an "advanced" expander so an operator can recover.

## Improvements over amalia-ai

These bugs in amalia-ai are explicitly fixed in our port:

- The `/api/whatsapp/channels` POST in amalia-ai requires `phone_number` but the connect button doesn't send it (`apps/web/app/(authenticated)/settings/ConnectWhatsAppButton.tsx:84-89`). Our `WhatsAppChannelProvisionIn.phone_number` is optional, and the panel forwards whatever `?client=` carried.
- The partner-callback HMAC fallback in amalia-ai accepts unsigned requests in production with a TODO comment (`apps/web/app/api/webhooks/360dialog/callback/route.ts:82-87`). Our (deferred) partner callback will mandate signature verification from day one — see the plan in `~/.claude/plans/360dialog-autonomous-channel-creation.md` stage 7.
- The partner-callback store lookup `whatsappPhoneNumberId === channelId` in amalia-ai is broken — those are different IDs. We index `meta.channel_id` separately on the integrations row so the same lookup works correctly.
- Two duplicate credential-exchange implementations exist in amalia-ai (the API route plus a server-component callback page). We do it once in `routers/integrations.py` and call it from one frontend component.
- Webhook config silent failure (`apps/web/app/api/whatsapp/channels/route.ts:210-221`): amalia-ai logs and continues if `POST /configs/webhook` fails. We surface that as a 4xx — a partial install means inbound messages won't route, which is worse than not finishing onboarding.

## Revisit if

- 360dialog ships a true server-side channel-creation API that bypasses Embedded Signup — we'd cut the popup and provision channels at merchant-creation time.
- Meta retires Embedded Signup in favour of something we host ourselves — we'd need Meta tech-provider status.
- Per-merchant API keys need to be rotated automatically on a schedule — we'd add a `rotate_channel_key` worker that calls `generate_channel_api_key` again and updates the encrypted column atomically.

## Where the work landed

Backend:
- `backend/libs/integrations/src/integrations/whatsapp/partner_client.py` — Partner Hub admin client (new file).
- `backend/libs/integrations/src/integrations/whatsapp/d360_client.py` — added `fetch_phone_number_id` + `configure_webhook`.
- `backend/libs/db/src/db/repositories/integration.py` — extended `upsert_whatsapp` with `api_key`, `channel_id`, `waba_id`; `resolve_whatsapp` now returns the decrypted `api_key`.
- `backend/services/api/src/api/routers/integrations.py` — added `GET /integrations/whatsapp/partner-id` and `POST /integrations/whatsapp/channels`.
- `backend/libs/ai_core/src/ai_core/conversation_service.py`, `actions/booking.py`, `workers/runtime.py`, `workers/scheduler/{no_answer,reactivation}.py` — thread per-channel `api_key` through to outbound sends.
- `backend/libs/integrations/src/integrations/whatsapp/factory.py` — placeholder fallback for legacy rows.

Frontend:
- `frontend/apps/web-merchant/src/components/integrations/connect-whatsapp-button.tsx` — popup launcher (new file).
- `frontend/apps/web-merchant/src/lib/whatsapp/parse-channels.ts` — handles 360dialog's quirky `[abc]` redirect format (new file).
- `frontend/apps/web-merchant/src/components/integrations/integrations-panel.tsx` — redirect-callback `useEffect` plus the new button; manual paste form kept behind "Inserisci manualmente".

Tests:
- `backend/tests/unit/test_partner_client.py` — Partner Hub wire shape.
- `backend/tests/unit/test_d360_client_helpers.py` — WABA helpers wire shape.

Operations:
- The 360dialog Partner Hub admin redirect URL must be set to `${PUBLIC_WEB_MERCHANT_URL}/integrations` once per environment.
