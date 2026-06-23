# Runbooks

Operational procedures — one file per scenario. Keep them terse and copy-pasteable.

## Available runbooks

- `go-live.md` — full production bring-up (provisioning + config). Include il **Custom Access Token hook** Supabase (il prerequisito che scrive `tenant_id`/`merchant_id`/`role` nei claim JWT) e l'**onboarding per-merchant**.
- `ecircuitbreaker-recovery.md` — recovery del circuit breaker.
- `migration-rollback.md` — rollback di una migrazione Alembic.
- `rotate-kek.md` — rotazione della KEK AES-256-GCM.
- `supabase-restore-drill.md` — PITR / restore drill (trimestrale, sez. 13.6).
- `fine-tune-deploy.md` — esecuzione della pipeline FT, promozione del modello, gestione fallimenti (anonymizer/spaCy/OpenAI).
- `ghl-reauth.md` — re-autenticazione GoHighLevel quando il token di una location è scaduto/revocato.

## Note operative cross-runbook

- **Auto-reply parte OFF** (scelta deliberata, `config_resolver/schema.py:204` `bot.auto_reply_enabled = False`, AND-ato con il flag per-thread `conversations.auto_reply`). UC-01 **non parte** finché il merchant non lo accende dal pannello bot durante l'onboarding (vedi `go-live.md` §7). È intenzionale: nessun bot risponde a un merchant appena creato.
- **Chiusura idle per estrazione obiezioni** (`workers/scheduler/close_conversations.py`): lo sweep `close_idle_conversations` è **policy di sistema**, tenant-agnostico. Legge la soglia dal `SYSTEM_DEFAULTS` (`CONVERSATION_IDLE_CLOSE_MINUTES`) e **non** onora l'override per-merchant via `ConfigResolver` (per design: lo sweep gira una volta su tutte le conversazioni, non per-merchant). Un override per-merchant cambia solo la vista per-merchant, non quando lo sweep chiude la conversazione.

## Planned topics

- `rls-isolation-check.md` — CI fixture with two tenants asserting cross-tenant reads fail (section 15 of the architecture doc).
- `restore-postgres.md` — vedi `supabase-restore-drill.md` (coperto).
- `whatsapp-onboarding.md` — Meta/360dialog verification walkthrough for a new merchant phone number.
