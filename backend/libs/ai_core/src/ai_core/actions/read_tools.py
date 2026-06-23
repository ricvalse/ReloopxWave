"""Read-only tool executor for the orchestrator's Amalia-style tool-use loop.

The orchestrator decides *when* to consult live data; this module performs the
read against GHL (calendar availability) or the local appointment mirror (the
lead's upcoming appointment) and returns an Italian summary the model reinjects
before composing a truthful reply. Strictly read-only: it never writes and never
messages the customer — that's what makes it safe to run mid-turn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from ai_core.actions.booking import (
    _format_human,
    _next_business_hour,
    _parse_iso,
    _resolve_tz,
)
from ai_core.orchestrator import OrchestratorAction, ToolResult
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AppointmentRepository,
    GHLMarketplaceRepository,
    IntegrationRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger

if TYPE_CHECKING:
    from ai_core.orchestrator import ConversationContext

logger = get_logger(__name__)


class GhlReadToolExecutor:
    """`ToolExecutor` backed by GHL + the appointment mirror."""

    def __init__(
        self,
        *,
        kek_base64: str,
        ghl_client_id: str,
        ghl_client_secret: str,
    ) -> None:
        self._kek = kek_base64
        self._client_id = ghl_client_id
        self._client_secret = ghl_client_secret

    async def execute_read(
        self, action: OrchestratorAction, ctx: ConversationContext
    ) -> ToolResult:
        if action.kind == "check_availability":
            return await self._check_availability(action, ctx)
        if action.kind == "lookup_appointment":
            return await self._lookup_appointment(ctx)
        return ToolResult(kind=action.kind, ok=False, summary="Strumento non riconosciuto.")

    # ---- check_availability ----------------------------------------------

    async def _check_availability(
        self, action: OrchestratorAction, ctx: ConversationContext
    ) -> ToolResult:
        # Resolve integration + config inside the session, carry scalars out,
        # then do the external GHL call WITHOUT holding the DB connection.
        worker_ctx = _worker_ctx(ctx)
        async with tenant_session(worker_ctx) as session:
            ghl = await IntegrationRepository(session, kek_base64=self._kek).resolve_ghl(
                ctx.merchant_id
            )
            if ghl is None:
                return ToolResult(
                    "check_availability",
                    False,
                    "Calendario non collegato: non posso verificare le disponibilità reali.",
                )
            config = ConfigResolver(session)
            calendar_id = action.payload.get("calendar_id") or await config.resolve(
                ConfigKey.BOOKING_DEFAULT_CALENDAR_ID, merchant_id=ctx.merchant_id
            )
            tz_name = (
                await config.resolve(ConfigKey.SCHEDULE_TIMEZONE, merchant_id=ctx.merchant_id)
                or "Europe/Rome"
            )
            lookahead = action.payload.get("lookahead_days") or await config.resolve(
                ConfigKey.BOOKING_LOOKAHEAD_DAYS, merchant_id=ctx.merchant_id
            )
            access_token = ghl.access_token
            refresh_token = ghl.refresh_token
            expires_at = ghl.expires_at
            location_id = ghl.location_id

        if not calendar_id:
            return ToolResult(
                "check_availability", False, "Nessun calendario è configurato per le prenotazioni."
            )

        tz = _resolve_tz(str(tz_name))
        preferred_iso = action.payload.get("preferred_start_iso")
        free = await self._fetch_slots(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            location_id=location_id,
            calendar_id=str(calendar_id),
            tz=tz,
            lookahead_days=int(lookahead) if lookahead else 14,
            preferred_iso=preferred_iso,
        )

        return ToolResult(
            "check_availability",
            True,
            _availability_summary(free, preferred_iso, tz),
            data={"free_slots": free},
        )

    async def _fetch_slots(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        location_id: str | None,
        calendar_id: str,
        tz: tzinfo,
        lookahead_days: int,
        preferred_iso: str | None,
    ) -> list[str]:
        async def _persist(bundle: GHLTokenBundle) -> None:
            if not bundle.location_id:
                return
            async with session_scope() as token_session:
                await GHLMarketplaceRepository(
                    token_session, kek_base64=self._kek
                ).set_location_token(
                    location_id=bundle.location_id,
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    expires_at=bundle.expires_at,
                )

        client = GHLClient(
            token_bundle=GHLTokenBundle(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                location_id=location_id,
            ),
            client_id=self._client_id,
            client_secret=self._client_secret,
            on_token_refresh=_persist,
        )
        try:
            pref = _parse_iso(preferred_iso, tz) if preferred_iso else None
            if pref is not None:
                start = pref - timedelta(hours=2)
                end = pref + timedelta(days=3)
            else:
                start = _next_business_hour(tz)
                end = start + timedelta(days=lookahead_days)
            slots = await client.get_free_slots(
                calendar_id, start_iso=start.isoformat(), end_iso=end.isoformat()
            )
            raw = [s.get("startTime") or s.get("start") for s in slots if s]
            return [s for s in raw if s]
        except IntegrationError:
            return []
        finally:
            await client.close()

    # ---- lookup_appointment ----------------------------------------------

    async def _lookup_appointment(self, ctx: ConversationContext) -> ToolResult:
        if ctx.lead_id is None:
            return ToolResult(
                "lookup_appointment", True, "Il cliente non ha appuntamenti futuri registrati."
            )
        worker_ctx = _worker_ctx(ctx)
        async with tenant_session(worker_ctx) as session:
            upcoming = await AppointmentRepository(session).list_upcoming_for_lead(
                merchant_id=ctx.merchant_id,
                lead_id=ctx.lead_id,
                now=datetime.now(tz=UTC),
            )
            whens = [a.start_at.isoformat() for a in upcoming]

        if not whens:
            return ToolResult(
                "lookup_appointment", True, "Il cliente non ha appuntamenti futuri registrati."
            )
        if len(whens) == 1:
            return ToolResult(
                "lookup_appointment",
                True,
                f"Il cliente ha 1 appuntamento futuro: {_format_human(whens[0])}.",
            )
        listed = "; ".join(_format_human(w) for w in whens)
        return ToolResult(
            "lookup_appointment",
            True,
            f"Il cliente ha {len(whens)} appuntamenti futuri: {listed}. "
            "Chiedi quale prima di procedere.",
        )


def _worker_ctx(ctx: ConversationContext) -> TenantContext:
    return TenantContext(
        tenant_id=ctx.tenant_id,
        merchant_id=ctx.merchant_id,
        role="worker",
        actor_id=ctx.merchant_id,
    )


def _availability_summary(free: list[str], preferred_iso: str | None, tz: tzinfo) -> str:
    human = [_format_human(s) for s in free[:5]]
    if not free:
        return "Nessuno slot libero nel periodo richiesto."
    if preferred_iso:
        pref = _parse_iso(preferred_iso, tz)
        is_free = any(_same_minute(_parse_iso(s, tz), pref) for s in free)
        if is_free:
            return (
                f"Lo slot richiesto ({_format_human(preferred_iso)}) è LIBERO: "
                "puoi proporre di confermarlo."
            )
        return (
            f"Lo slot richiesto ({_format_human(preferred_iso)}) NON è libero. "
            f"Disponibilità reali più vicine: {'; '.join(human)}."
        )
    return f"Disponibilità reali: {'; '.join(human)}."


def _same_minute(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return False
    return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)
