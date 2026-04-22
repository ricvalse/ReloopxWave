"""Supabase Storage REST client — lightweight, service-role only.

Used by the indexer (to fetch KB uploads) and the analytics exporter (to drop
signed CSVs). Going direct to Storage via HTTP avoids the full supabase-py
dependency for a couple of endpoints.
"""
from __future__ import annotations

import httpx

from shared import IntegrationError, get_logger

logger = get_logger(__name__)


class SupabaseStorage:
    def __init__(self, *, project_url: str, service_role_key: str, bucket: str) -> None:
        if not project_url or not service_role_key:
            raise IntegrationError(
                "Supabase Storage requires project URL + service role key",
                error_code="storage_not_configured",
            )
        self._base = project_url.rstrip("/") + "/storage/v1"
        self._auth = {"Authorization": f"Bearer {service_role_key}", "apikey": service_role_key}
        self._bucket = bucket

    async def download(self, path: str) -> bytes:
        url = f"{self._base}/object/{self._bucket}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=self._auth)
            if resp.status_code >= 400:
                raise IntegrationError(
                    f"Supabase Storage download failed ({resp.status_code})",
                    error_code="storage_download_failed",
                )
            return resp.content

    async def create_signed_url(self, path: str, *, expires_in_seconds: int = 3600) -> str:
        url = f"{self._base}/object/sign/{self._bucket}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers={**self._auth, "Content-Type": "application/json"},
                json={"expiresIn": expires_in_seconds},
            )
            if resp.status_code >= 400:
                raise IntegrationError(
                    f"Supabase Storage sign failed ({resp.status_code})",
                    error_code="storage_sign_failed",
                )
            signed = resp.json().get("signedURL") or resp.json().get("signedUrl", "")
            if not signed:
                raise IntegrationError("sign response missing signedURL", error_code="storage_sign_missing")
            return self._base.rsplit("/storage/v1", 1)[0] + "/storage/v1" + signed
