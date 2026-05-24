# Router-side changes — handoff prompt

Paste **everything below the `---` separator** into a Claude Code session whose
working directory is `/Users/riccardo/Progetti/WhatsappRouter`. The prompt is
written for an agent that has not seen the platform-side work; it tells the
agent exactly what to read, what to change, and what to verify.

The matching platform-side commit lives in
`/Users/riccardo/Progetti/ReloopxWave`. Cross-reference these files if needed
(they are short):

- `backend/libs/integrations/src/integrations/router/signatures.py`
- `backend/libs/integrations/src/integrations/router/client.py`
- `backend/services/api/src/api/routers/webhooks.py`
- `backend/services/api/src/api/routers/internal.py`
- `backend/services/api/src/api/routers/integrations.py`

---

# Task: router-side changes to match the ReloopxWave platform update

You are working in the WhatsappRouter repo. The Wave Marketing / ReloopxWave
platform (in a sibling repo at `/Users/riccardo/Progetti/ReloopxWave`) has
already shipped its half of a coordinated four-part change. Your job is to
ship the router half so the two sides match.

**The four changes are mandatory and must all be in this PR.** Do not skip
any of them. Do not pick and choose. Do not declare success until all four
sections plus the verification step pass.

## Phase 0 — Read these files before editing anything

Run a `Read` against each of these and quote the relevant lines back to me
when you describe your plan:

1. `FLOWS.md` — the whole file. This is the contract.
2. `src/router/api/webhook.py`
3. `src/router/workers/forward.py`
4. `src/router/api/onboard.py`
5. `src/router/api/admin.py`
6. `src/router/db/` (or wherever the SQLAlchemy/asyncpg models for
   `platform_registry` and `waba_mapping` live)
7. Any `src/router/signatures.py` / `src/router/security.py` / module that
   defines the existing `X-Amaliatech-Signature` header constant
8. `tests/` — list the test files that touch signature verification or
   `/onboard/start`

Also run these commands and paste the output before starting:

```bash
grep -rn "X-Amaliatech-Signature\|x-amaliatech-signature\|X-AMALIATECH-SIGNATURE" src tests docs 2>/dev/null
grep -rni "amalia\|amaliatech" src tests docs 2>/dev/null
grep -rn "onboard/start\|onboard_start" src tests 2>/dev/null
grep -rn "connect_url" src tests 2>/dev/null
```

The grep output tells you the exact list of sites you have to touch. Do
not guess from filenames.

After you have read these and produced the grep output, give me a numbered
list of every file you plan to modify, with one sentence per file
describing the change. **Wait for my "go" before editing any file.**

## Phase 1 — Rename signature header

Old constant value: `X-Amaliatech-Signature`. New value:
**`X-Relooptech-Signature`**. The hex digest format (`sha256=<hex>`) and the
HMAC-SHA256-over-raw-body scheme do not change. Only the header string.

Apply the rename at every site the grep in Phase 0 returned. The likely set:

- The constant module (`SIGNATURE_HEADER = "X-Relooptech-Signature"`).
- `src/router/workers/forward.py` — when building the outbound POST to the
  platform's `webhook_url`, set this header on the request.
- `src/router/api/onboard.py` — when firing `whatsapp.connected` or
  `whatsapp.key_rotated` to the platform's notify URL, set this header.
- `src/router/api/admin.py` — `rotate-key` handler that re-fires the notify.
- Tests that build a signed request or assert the header name.

**Do not** touch `X-Hub-Signature-256` in `src/router/api/webhook.py` — that
is 360dialog's header for inbound traffic from them to the router, which is
a separate trust boundary. Verify with grep before and after that
`X-Hub-Signature-256` count is unchanged.

Also strip every literal "Amalia" / "Amaliatech" / "amalia-ai" from comments,
log keys, error strings, env var names, docstring examples, and READMEs.
The platform's owner is **Relooptech** (relooptech.ai). The grep in Phase 0
should already list every site. The only thing you must NOT rename is the
column / field `shared_secret` — that name is already neutral and the
platform refers to it by that exact name.

## Phase 2 — Authenticate `POST /onboard/start` with HMAC

Today, `/onboard/start` is unauthenticated (see `FLOWS.md:82` —
*"`/onboard/start` requires no auth"*). The platform now signs its request
body and expects the router to verify. **You must add verification.**

The platform's request looks exactly like this (verbatim from
`backend/libs/integrations/src/integrations/router/client.py:62-83` in the
platform repo):

```python
body = json.dumps(
    {
        "platform_id": platform_id,
        "customer_id": str(customer_id),
        "return_url": return_url,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
signature = sign_router_payload(raw_body=body, shared_secret=self._shared_secret)
# header: X-Relooptech-Signature: sha256=<hex>
```

Where `sign_router_payload` is:

```python
import hashlib, hmac
def sign_router_payload(*, raw_body: bytes, shared_secret: str) -> str:
    digest = hmac.new(shared_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
```

Implement the verification in the `/onboard/start` handler in
`src/router/api/onboard.py` with this exact sequence — order matters:

1. Read raw request bytes (`raw = await request.body()`) **before**
   anything else. Do not call `await request.json()` first; that consumes
   the stream and re-serialization would change the bytes you need to
   verify.
2. Parse `platform_id` from a one-off JSON load of `raw`. If JSON is
   malformed → `400`.
3. Read the `X-Relooptech-Signature` header. If missing → `401`.
4. `SELECT shared_secret FROM platform_registry WHERE platform_id = $1
   AND status = 'active'`. If no row → `401` (not `404` — do not leak
   which `platform_id`s are valid).
5. Strip the `sha256=` prefix from the header value. Compute
   `hmac.new(shared_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()`.
   Compare with `hmac.compare_digest`. If lengths differ or values differ
   → `401`.
6. **Only after** the signature passes, parse the full body and proceed
   with the existing state-minting logic.

Rules:

- On any 401 path, log a structured event (`onboard.start.signature_rejected`
  with `bytes=len(raw)`, `has_header=bool(header)`, `platform_id=...`). Do
  NOT log the raw body. Do NOT log the secret.
- Add a Prometheus counter
  `router_onboard_start_unauthorized_total{reason=...}` with reason values
  `missing_header`, `unknown_platform`, `bad_signature`, `bad_payload`.
- Add a Slack rate-alert on `bad_signature` spikes, mirroring the existing
  `router_events_unauthorized_total` alert.
- Reuse / extract a single `verify_router_signature` helper if the router
  already has an equivalent for the inbound `whatsapp.connected` notify
  going the other direction. Don't duplicate the HMAC code.

Tests to add (in the same PR):

- Valid signature → `200` with `{connect_url, state, expires_in}` (see
  Phase 3 for `connect_url`).
- Missing `X-Relooptech-Signature` → `401`.
- Header present but wrong secret → `401`.
- Header present, correct secret, but body tampered after signing → `401`.
- Header present, correct format, but `platform_id` not in
  `platform_registry` → `401`.
- Header value missing the `sha256=` prefix → `401`.

Update `FLOWS.md:78–87` to document that `/onboard/start` now requires
`X-Relooptech-Signature` matching the platform's `shared_secret`.

## Phase 3 — Return `connect_url` from `/onboard/start`

Today `/onboard/start` returns only `{state, expires_in}` and the platform
reconstructs the 360dialog hub URL on its side using its own copy of the
partner_id. This violates the FLOWS.md contract, which says explicitly:

> Returns `{connect_url, state, expires_in}`. The platform redirects the
> merchant straight to `connect_url`; `partner_id` is owned by the router
> and never leaves it.

Fix it: build the connect URL server-side in the handler and include it in
the response.

The connect URL must be:

```
https://hub.360dialog.com/dashboard/app/<DIALOG360_PARTNER_ID>/permissions?redirect_url=<encoded callback>
```

Where the `redirect_url` query value (whole-URL-encoded so its own `?` and
`&` survive transit) is:

```
<ROUTER_PUBLIC_BASE_URL>/onboard/callback?platform=<platform_id>&state=<state>
```

`ROUTER_PUBLIC_BASE_URL` should resolve to `https://router.relooptech.ai`
in production — see Phase 4. In Python:

```python
from urllib.parse import quote, urlencode

router_callback = (
    f"{settings.router_public_base_url.rstrip('/')}/onboard/callback?"
    + urlencode({"platform": platform_id, "state": state})
)
connect_url = (
    f"https://hub.360dialog.com/dashboard/app/"
    f"{quote(settings.dialog360_partner_id, safe='')}/permissions?"
    + urlencode({"redirect_url": router_callback})
)
return {"connect_url": connect_url, "state": state, "expires_in": ttl}
```

Update tests asserting the response shape. Update `FLOWS.md` Phase B section
if it currently shows only `{state, expires_in}`.

This change is **backward-compatible** for the platform — the platform's
`RouterClient.onboard_start` will be updated in a follow-up to consume
`connect_url`. Until that follow-up ships, the platform will ignore the
extra field. Do not block on it.

## Phase 4 — Make `router.relooptech.ai` canonical

The router will be served at `router.relooptech.ai`. In code:

- Find every hard-coded hostname (`hub.360dialog.com` is fine; what you're
  looking for are router-side hostnames). Replace with a setting
  `ROUTER_PUBLIC_BASE_URL` if not already there.
- Update `TrustedHostMiddleware` / CORS allowed origins to include
  `router.relooptech.ai`.
- Update README / FLOWS.md examples that hard-code the old hostname.
- In Railway config (if checked in): note in the PR description which env
  vars need to flip on production. **Do not commit secrets.**

DNS cutover and the 360dialog redirect-URL allowlist are operational
(see Phase 6). Do not attempt them from code.

## Phase 5 — Verification

Before declaring done:

1. `grep -rn "X-Amaliatech-Signature\|amalia\|amaliatech" src tests docs`
   returns nothing.
2. `grep -rn "X-Relooptech-Signature" src tests` returns every site the
   Phase 0 grep originally returned, plus the new `/onboard/start` handler.
3. Run the full test suite. All tests pass, including the new ones from
   Phase 2 and the updated response-shape assertions from Phase 3.
4. Boot the router locally (or against a staging Railway) and:
   - `curl -X POST /onboard/start` with no signature → 401 in logs as
     `missing_header`.
   - Same call with a valid signature → 200 with `connect_url`.

If any of these fail, fix the underlying cause — do not patch around it.

## Phase 6 — Cutover checklist (for the user, not for you)

Include this verbatim in the PR description so the person merging knows
what to do operationally. Do not try to execute it yourself.

> Before merging:
>
> 1. Deploy the matching ReloopxWave commit first (or in lockstep with
>    this PR). Until both sides ship together, forwards from the router
>    will fail HMAC verification on the platform side (header rename), and
>    `/onboard/start` will start 401ing the platform if this PR is live
>    and the platform hasn't been updated.
> 2. After both sides are deployed: DNS cutover `router.relooptech.ai` →
>    Railway `router-api` service.
> 3. In the 360dialog Partner dashboard, add
>    `https://router.relooptech.ai/onboard/callback` to the
>    `redirect_url` allowlist **before** the next merchant onboarding.
> 4. Confirm `platform_registry.shared_secret` for the `wavemarketing`
>    row matches `ROUTER_SHARED_SECRET` on the ReloopxWave Railway api
>    service. If router→platform forwards have ever authenticated
>    successfully, this is already true and no action is needed.
> 5. Smoke test: send a WhatsApp message to an onboarded number, confirm
>    `webhook.router.inbound` log on the platform with no 401. Then run
>    an end-to-end onboard for a fresh test number through the new
>    `connect_url` path and confirm `router.notify.channel_persisted` on
>    the platform.

## Process

- Work on a branch named `feat/relooptech-signature-and-signed-onboard`.
  Do not push to main.
- Commit each phase separately so reviewers can read the diff in pieces.
- Do not skip Phase 0. Reading first, editing second.
- If something in the router looks broken or stale **beyond** the four
  phases above (dead code, missing tests on existing flows, etc.),
  surface it in the PR description but do not fix it in this PR.
- When done, write a PR description that:
  - Lists every file touched.
  - Quotes the Phase 0 grep output and the Phase 5 grep output so the
    reviewer can see the rename is complete.
  - Includes the Phase 6 cutover checklist verbatim.
