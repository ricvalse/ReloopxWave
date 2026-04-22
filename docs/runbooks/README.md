# Runbooks

Operational procedures — one file per scenario. Keep them terse and copy-pasteable.

## Planned topics

- `rls-isolation-check.md` — CI fixture with two tenants asserting cross-tenant reads fail (section 15 of the architecture doc).
- `supabase-auth-hook.md` — the SQL Auth hook that writes `tenant_id`, `merchant_id`, `role` into JWT claims.
- `fine-tune-deploy.md` — end-to-end procedure for running the FT pipeline and promoting a model.
- `restore-postgres.md` — PITR / on-demand backup restore drill (quarterly, section 13.6).
- `whatsapp-onboarding.md` — Meta verification walkthrough for a new merchant phone number.
- `ghl-reauth.md` — what to do when a merchant's GHL OAuth token expires.
