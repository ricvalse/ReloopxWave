# Runbook — Rotate `INTEGRATIONS_KEK_BASE64`

The KEK (key-encryption key) is what lets the backend decrypt per-merchant GHL and WhatsApp secrets stored in `integrations.secret_ciphertext`. Losing or rotating it requires re-encrypting every active row.

## When to rotate

- Suspected KEK exposure (env var leaked to logs, accidentally committed, personnel change with access).
- Annual rotation hygiene.

## Procedure

### 1. Generate a new KEK

```bash
uv run python -c "from shared.crypto import generate_kek_base64; print(generate_kek_base64())"
```

A 44-char base64 string.

### 2. Put both KEKs on the API, old one first

```bash
railway variables set INTEGRATIONS_KEK_BASE64_PREV="$OLD_KEK" --service api
railway variables set INTEGRATIONS_KEK_BASE64="$NEW_KEK" --service api
# same on worker
```

> V1 code reads only `INTEGRATIONS_KEK_BASE64`. The `_PREV` env is surfaced only for the migration script below — the application does **not** fall back to it at runtime.

### 3. Run the re-encryption script

(To be written when rotation becomes necessary — leaving the shape here so the first rotation has a concrete starting point.)

```python
# scripts/rotate_kek.py (draft)
# for each row in integrations where kek_version = 1:
#   plaintext = decrypt with PREV
#   reenc = encrypt with NEW
#   update row (secret_ciphertext, secret_nonce, secret_aad, kek_version=2)
```

Run it with `railway run --service api python scripts/rotate_kek.py` so it executes inside the API container's env.

### 4. Drop the old KEK

Once `SELECT count(*) FROM integrations WHERE kek_version = 1` returns 0:

```bash
railway variables delete INTEGRATIONS_KEK_BASE64_PREV --service api
railway variables delete INTEGRATIONS_KEK_BASE64_PREV --service worker
```

### 5. Verify

- `/integrations/status` for a known-good merchant still reports `connected`.
- `railway logs --service worker | grep whatsapp` shows no `decryption_failed` errors on the next inbound message.

## Don't

- Don't rotate during a merchant's active onboarding flow — OAuth callbacks that arrive mid-rotation race against the re-encryption script.
- Don't lose the old KEK before the script completes — the ciphertexts are unrecoverable.
