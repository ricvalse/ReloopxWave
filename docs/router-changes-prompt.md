# Router-side changes — handoff prompt

Paste the content below into a Claude Code session pointed at
`/Users/riccardo/Progetti/WhatsappRouter`. It describes the three coordinated
changes the router must ship to match the platform-side commits made in
`/Users/riccardo/Progetti/ReloopxWave` (signed `/onboard/start`,
`X-Relooptech-Signature` header rename, `router.relooptech.ai` cutover).

---

Context: I just updated the **Wave Marketing / ReloopxWave platform**
(`/Users/riccardo/Progetti/ReloopxWave`) to match a tighter router contract.
The router side now needs three coordinated changes. The platform-side commit
is local-only right now — refer to `RouterClient`, `webhooks.py`, and
`internal.py` in that repo if you need to inspect the canonical wire shape.

## 1. Rename the signature header everywhere

Old name: `X-Amaliatech-Signature`. New name: **`X-Relooptech-Signature`**.
Same `sha256=<hex>` value format, same HMAC-SHA256-over-raw-body scheme —
only the header string changes.

Touch every site that reads OR writes this header:

- `src/router/api/webhook.py` — verification of the 360dialog inbound (this is
  `X-Hub-Signature-256` from 360dialog, **NOT** the rename target; leave
  alone). The rename applies to *outbound* signing to platforms.
- `src/router/workers/forward.py` — when signing the body before POSTing to
  `platform.webhook_url`, send the new header name.
- `src/router/api/onboard.py` — when firing `whatsapp.connected` /
  `whatsapp.key_rotated` to `platform.notify_url` (or whichever field your
  model uses for the platform-side notify endpoint), sign with the new
  header.
- `src/router/api/admin.py` — `rotate-key` handler that re-fires the notify.
- Any constants module (e.g. `src/router/signatures.py`) defining
  `SIGNATURE_HEADER`.
- Tests under `tests/` that assert the header name.
- Any docs (`FLOWS.md`, README) that mention `X-Amaliatech-Signature`.

Also strip any "Amalia" / "Amaliatech" branding from comments, log fields,
error strings, env var names. The company that owns this router is
**Relooptech** (relooptech.ai). Do NOT rename the `shared_secret` field —
that's already neutral.

## 2. Authenticate `POST /onboard/start` with HMAC

The platform now signs its `POST /onboard/start` body with
`X-Relooptech-Signature: sha256=<hex>` using the platform's `shared_secret`.
The router must verify it before minting state. This closes the trust gap
mentioned in `FLOWS.md:82` (currently unauthenticated).

Wire shape — match this exactly:

1. Read raw request bytes (`await request.body()` or framework equivalent)
   **before** parsing JSON.
2. Parse just enough to extract `platform_id` from the body. The platform
   serializes deterministically with sorted keys and no whitespace, so a
   re-serialization round-trip would change the bytes and break the HMAC —
   verify against the raw bytes.
3. Look up `platform_registry.shared_secret WHERE platform_id = $1`.
4. Compute `hmac_sha256(shared_secret, raw_body).hexdigest()`, compare in
   constant time against the hex after the `sha256=` prefix.
5. 401 on missing header, unknown `platform_id`, or mismatched digest. Do
   NOT log the body on rejection (matches the 360dialog signature-rejection
   posture in `webhook.py`).
6. Increment a Prometheus counter for unauthorized `/onboard/start` calls
   and Slack-alert on rate spikes, mirroring the existing
   `router_events_unauthorized_total`.

For reference, the platform's signing code is in
`backend/libs/integrations/src/integrations/router/signatures.py`
(`sign_router_payload`) and
`backend/libs/integrations/src/integrations/router/client.py`
(`RouterClient.onboard_start`) — the body is
`json.dumps({platform_id, customer_id, return_url}, sort_keys=True, separators=(",", ":")).encode("utf-8")`.

Add a test that:

- Valid signature → 200 with `{state, expires_in, connect_url}`.
- Missing header → 401.
- Wrong secret → 401.
- Body bytes tampered post-signing → 401.

Update `FLOWS.md:78–87` to document the new auth requirement.

## 3. Make `router.relooptech.ai` the canonical public URL

The router will be served at `router.relooptech.ai`. Audit and update:

- Any `ALLOWED_HOSTS` / CORS / trusted-host config (FastAPI
  `TrustedHostMiddleware`, etc.).
- `DIALOG360_REDIRECT_BASE` or equivalent — the value used to build the
  `redirect_url` query param on the 360dialog hub URL must resolve to
  `https://router.relooptech.ai/onboard/callback`.
- Documentation / README examples that hard-code an old hostname.
- Railway service variables — flag any that need to change but don't
  commit secrets.

DNS itself is out of scope (you'll cut that over manually); just make sure
the code is hostname-agnostic where it can be, and points at
`router.relooptech.ai` where it must be explicit.

## Process

- Work on a branch, not main.
- Run the existing test suite after each of the three sections.
- Surface anything you find in the router that's broken or stale beyond
  these three items, but don't fix it without checking first.
- When done, produce a short PR description listing every file touched and
  any follow-up the platform side will need (e.g. if you decide to also
  rename `shared_secret` to something — don't, but call it out if you'd
  recommend it).
