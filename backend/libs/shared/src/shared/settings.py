from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    ghl_client_id: str = ""
    ghl_client_secret: str = ""
    ghl_webhook_secret: str = ""
    ghl_oauth_state_secret: str = ""
    ghl_redirect_uri: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_graph_base_url: str = "https://graph.facebook.com/v21.0"

    public_api_base_url: str = ""
    public_web_merchant_url: str = ""

    integrations_kek_base64: str = Field(
        default="", description="AES-256-GCM key-encryption key, base64-encoded"
    )

    sentry_dsn_backend: str = ""
    posthog_key: str = ""
    posthog_host: str = "https://eu.posthog.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
