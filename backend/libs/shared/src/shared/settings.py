from functools import lru_cache
from typing import ClassVar, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# GHL marketplace webhook signing keys are GLOBAL published constants (same for
# every Marketplace app, not secrets), so we ship them as defaults and let env
# override them if GHL ever rotates. Source: GHL Webhook Integration Guide
# (marketplace.gohighlevel.com/docs/webhook/WebhookIntegrationGuide).
#   - Ed25519 → header `x-ghl-signature` (current/preferred).
#   - RSA-SHA256 → header `x-wh-signature` (legacy, deprecated by GHL 2026-07-01).
# RSA key SHA-256(DER) fingerprint: 2f62045d413b1b7747e26770ce7f57840cbf86288cd20ceb90d299a62501fd25
_GHL_MARKETPLACE_ED25519_PUBKEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=\n"
    "-----END PUBLIC KEY-----\n"
)
_GHL_MARKETPLACE_RSA_PUBKEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAokvo/r9tVgcfZ5DysOSC"
    "Frm602qYV0MaAiNnX9O8KxMbiyRKWeL9JpCpVpt4XHIcBOK4u3cLSqJGOLaPuXw6"
    "dO0t6Q/ZVdAV5Phz+ZtzPL16iCGeK9po6D6JHBpbi989mmzMryUnQJezlYJ3DVfB"
    "csedpinheNnyYeFXolrJvcsjDtfAeRx5ByHQmTnSdFUzuAnC9/GepgLT9SM4nCpvu"
    "xmZMxrJt5Rw+VUaQ9B8JSvbMPpez4peKaJPZHBbU3OdeCVx5klVXXZQGNHOs8gF3k"
    "voV5rTnXV0IknLBXlcKKAQLZcY/Q9rG6Ifi9c+5vqlvHPCUJFT5XUGG5RKgOKUJ06"
    "2fRtN+rLYZUV+BjafxQauvC8wSWeYja63VSUruvmNj8xkx2zE/Juc+yjLjTXpIocm"
    "aiFeAO6fUtNjDeFVkhf5LNb59vECyrHD2SQIrhgXpO4Q3dVNA5rw576PwTzNh/AMf"
    "HKIjE4xQA1SZuYJmNnmVZLIZBlQAF9Ntd03rfadZ+yDiOXCCs9FkHibELhCHULgCs"
    "nuDJHcrGNd5/Ddm5hxGQ0ASitgHeMZ0kcIOwKDOzOU53lDza6/Y09T7sYJPQe7z0c"
    "vj7aE4B+Ax1ZoZGPzpJlZtGXCsu9aTEGEnKzmsFqwcSsnw3JB31IGKAykT1hhTiaC"
    "eIY/OwwwNUY2yvcCAwEAAQ==\n"
    "-----END PUBLIC KEY-----\n"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:54322/postgres"

    @field_validator("supabase_db_url", mode="before")
    @classmethod
    def _normalise_db_url(cls, v: str | None) -> str:
        """Supabase emits `postgres://...` or `postgresql://...`; asyncpg needs the
        explicit driver prefix. Rewrite both to `postgresql+asyncpg://...` so users
        can paste the Supabase connection string verbatim into Railway.
        """
        if not v:
            return v or ""
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        return v

    supabase_kb_bucket: str = "kb-documents"
    supabase_ft_bucket: str = "ft-training-data"
    supabase_exports_bucket: str = "analytics-exports"

    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_fallback_enabled: bool = False

    # LLM model ids — overridable via env so a provider rename or a tier change
    # doesn't require a code change (the hardcoded gpt-5.x names were the single
    # biggest "every call 404s" risk at go-live). Defaults match spec 6.7.
    llm_model_default: str = "gpt-5-mini"
    llm_model_escalation: str = "gpt-5.2"
    llm_model_sentiment: str = "gpt-5-nano"
    llm_model_fallback: str = "claude-sonnet-4-6"
    llm_model_embedding: str = "text-embedding-3-small"

    ghl_client_id: str = ""
    ghl_client_secret: str = ""
    ghl_webhook_secret: str = ""
    ghl_oauth_state_secret: str = ""
    ghl_redirect_uri: str = ""
    # PEM public keys GHL uses to sign marketplace INSTALL/UNINSTALL webhooks —
    # global published constants (defaulted above), override via env only if GHL
    # rotates them. Distinct from `ghl_webhook_secret` (HMAC, data webhooks).
    #   - ed25519 → header `x-ghl-signature` (current/preferred).
    #   - rsa → header `x-wh-signature` (legacy, deprecated by GHL 2026-07-01).
    ghl_marketplace_public_key: str = _GHL_MARKETPLACE_RSA_PUBKEY_PEM
    ghl_marketplace_public_key_ed25519: str = _GHL_MARKETPLACE_ED25519_PUBKEY_PEM

    # WhatsApp is mediated by the Wave Marketing router (a 360dialog Partner
    # owned by the platform team). Wave Marketing no longer holds a Partner
    # API key itself — the router mints per-channel D360 keys during
    # onboarding and delivers them to us via the signed
    # `POST /internal/whatsapp-connected` notify. See NEWPLATFORM_SETUP.md.
    router_base_url: str = ""
    router_shared_secret: str = ""
    router_platform_id: str = "wavemarketing"

    public_api_base_url: str = ""
    public_web_admin_url: str = ""
    public_web_merchant_url: str = ""

    # Comma-separated allowlist for CORS. If empty we fall back to the two
    # `public_web_*_url` values. `"*"` is deliberately NOT supported in prod
    # because `allow_credentials=True` forbids the wildcard.
    cors_allowed_origins: str = ""

    # Comma-separated Host header allowlist for TrustedHostMiddleware. Empty =
    # not enforced (a warning is logged in non-local). Use the bare hostnames,
    # e.g. "api.reloop.example,reloop.up.railway.app".
    allowed_hosts: str = ""

    # Fixed-window rate limit (requests/minute per client IP) applied to the
    # public, unauthenticated surface (webhooks, OAuth callback, internal notify).
    # 0 disables the limiter. Fail-open if Redis is unreachable.
    rate_limit_public_per_min: int = 120

    integrations_kek_base64: str = Field(
        default="", description="AES-256-GCM key-encryption key, base64-encoded"
    )

    sentry_dsn_backend: str = ""
    posthog_key: str = ""
    posthog_host: str = "https://eu.posthog.com"

    # Secrets without which the platform cannot function at all. Empty (or, for
    # the URLs, a localhost default) in production is a hard boot error.
    _PROD_REQUIRED_SECRETS: ClassVar[tuple[str, ...]] = (
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "openai_api_key",
        "integrations_kek_base64",
    )
    # Integration creds the platform boots without, but a tenant can't use the
    # corresponding feature until they're set — surfaced as a startup warning.
    _PROD_RECOMMENDED: ClassVar[tuple[str, ...]] = (
        "ghl_client_id",
        "ghl_client_secret",
        # ghl_marketplace_public_key(_ed25519) ship as built-in defaults, so no
        # warning is needed — they're never empty unless explicitly overridden.
        "router_base_url",
        "router_shared_secret",
        "sentry_dsn_backend",
    )

    def production_config_errors(self) -> list[str]:
        """Hard misconfigurations that must block boot in production."""
        errors: list[str] = []
        for name in self._PROD_REQUIRED_SECRETS:
            if not str(getattr(self, name, "") or "").strip():
                errors.append(f"{name} is empty")
        for name in ("supabase_db_url", "redis_url"):
            value = str(getattr(self, name, "") or "")
            if "localhost" in value or "127.0.0.1" in value:
                errors.append(f"{name} points at localhost")
        return errors

    def production_config_warnings(self) -> list[str]:
        """Soft gaps: boot proceeds, but a feature stays inert until set."""
        return [
            f"{name} is empty"
            for name in self._PROD_RECOMMENDED
            if not str(getattr(self, name, "") or "").strip()
        ]

    def ensure_production_ready(self) -> None:
        """Fail fast at startup if a required secret is missing in production.

        No-op outside `production` so local/test/staging boot with defaults.
        """
        if self.environment != "production":
            return
        errors = self.production_config_errors()
        if errors:
            raise RuntimeError("Invalid production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
