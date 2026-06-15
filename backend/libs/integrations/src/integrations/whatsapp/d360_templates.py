"""360dialog template management client.

Template *sending* lives in `d360_client.py` (the `/messages` endpoint). This
module covers template *lifecycle* — submit for approval, poll status, delete —
which 360dialog exposes under the WABA management API (`/v1/configs/templates`),
authenticated with the same per-channel `D360-API-KEY`.

Submit shape (POST /v1/configs/templates):
    {"name", "category", "language", "components": [...]}
Status fetch (GET /v1/configs/templates?name=<name>):
    {"waba_templates": [{"status", "category", "quality_score", "rejected_reason", "id"}]}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from integrations.whatsapp.d360_client import D360_BASE
from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class TemplateStatus:
    """Approval state of a template as reported by 360dialog/Meta."""

    name: str
    status: str  # PENDING | APPROVED | REJECTED | PAUSED | DISABLED | ...
    category: str | None
    quality_score: str | None  # HIGH | MEDIUM | LOW
    rejected_reason: str | None
    whatsapp_template_id: str | None


def map_meta_status_to_local(meta_status: str | None) -> str:
    """Map a 360dialog/Meta template status onto our local lifecycle value."""
    s = (meta_status or "").upper()
    if s == "APPROVED":
        return "approved"
    if s in ("REJECTED", "DISABLED"):
        return "rejected"
    # PENDING | PAUSED | FLAGGED | IN_APPEAL | anything else → still in review
    return "pending_approval"


class D360TemplateClient:
    """Manage WhatsApp templates for one channel (per-merchant `D360-API-KEY`)."""

    def __init__(
        self,
        *,
        api_key: str,
        http: httpx.AsyncClient | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(base_url=base_url or D360_BASE, timeout=20.0)

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {"D360-API-KEY": self._api_key, "Content-Type": "application/json"}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def create_template(
        self,
        *,
        name: str,
        category: str,
        language: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Submit a template for approval. Returns the raw 360dialog response."""
        resp = await self._http.post(
            "/v1/configs/templates",
            json={
                "name": name,
                "category": category,
                "language": language,
                "components": components,
            },
            headers=self._headers,
        )
        if resp.status_code >= 400:
            raise IntegrationError(
                f"360dialog template create failed ({resp.status_code})",
                error_code="d360_template_create_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        result: dict[str, Any] = resp.json()
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0),
        reraise=True,
    )
    async def fetch_template_status(self, *, name: str) -> TemplateStatus | None:
        """Poll the approval status for `name`. None when the template is unknown."""
        resp = await self._http.get(
            "/v1/configs/templates",
            params={"name": name},
            headers=self._headers,
        )
        if resp.status_code >= 400:
            raise IntegrationError(
                f"360dialog template status failed ({resp.status_code})",
                error_code="d360_template_status_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
        payload: dict[str, Any] = resp.json()
        templates = payload.get("waba_templates") or payload.get("template") or []
        if not templates:
            return None
        # Pick the entry that matches the name (the endpoint may return several languages).
        entry = next((t for t in templates if t.get("name") == name), templates[0])
        return TemplateStatus(
            name=str(entry.get("name", name)),
            status=str(entry.get("status", "")),
            category=entry.get("category"),
            quality_score=entry.get("quality_score"),
            rejected_reason=entry.get("rejected_reason") or entry.get("rejection_reason"),
            whatsapp_template_id=(str(entry["id"]) if entry.get("id") else None),
        )

    async def delete_template(self, *, name: str) -> bool:
        """Delete a template by name. Returns True on success."""
        resp = await self._http.delete(
            "/v1/configs/templates",
            params={"name": name},
            headers=self._headers,
        )
        if resp.status_code >= 400:
            logger.warning("d360.template_delete_failed", name=name, status=resp.status_code)
            return False
        return True
