"""UC-04 — move_pipeline action handler.

The orchestrator emits `move_pipeline` when a lead's conversation state crosses
the qualification threshold (or when the lead explicitly signals readiness).

Inputs in payload:
  - stage_id  (optional; defaults to merchant's `pipeline.qualified_stage_id`)
  - pipeline_id (optional; looked up from GHL if missing)
  - opportunity_id (optional; created if missing)
  - reason (logged for audit)
  - value, currency (optional; attached to opportunity)

Side effects:
  - Upsert GHL contact + opportunity.
  - Move the opportunity to the configured stage.
  - Persist `lead.pipeline_stage_id`.
  - Emit `pipeline.moved` or `pipeline.failed` analytics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_core.orchestrator import OrchestratorAction
from config_resolver import ConfigKey, ConfigResolver
from db import (
    AnalyticsRepository,
    IntegrationRepository,
    LeadRepository,
    TenantContext,
    session_scope,
    tenant_session,
)
from integrations.ghl.client import GHLClient, GHLTokenBundle
from shared import IntegrationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class MoveOutcome:
    moved: bool
    stage_id: str | None
    opportunity_id: str | None
    reason: str | None


class MovePipelineHandler:
    kind = "move_pipeline"

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

    async def __call__(self, action: OrchestratorAction, turn_ctx) -> None:
        worker_ctx = TenantContext(
            tenant_id=turn_ctx.tenant_id,
            merchant_id=turn_ctx.merchant_id,
            role="worker",
            actor_id=turn_ctx.merchant_id,
        )

        outcome: MoveOutcome
        async with tenant_session(worker_ctx) as session:
            ghl_repo = IntegrationRepository(session, kek_base64=self._kek)
            leads = LeadRepository(session)
            analytics = AnalyticsRepository(session)
            config = ConfigResolver(session)

            ghl = await ghl_repo.resolve_ghl(turn_ctx.merchant_id)
            if ghl is None:
                outcome = MoveOutcome(False, None, None, "no_ghl_integration")
            else:
                stage_id = action.payload.get("stage_id") or await config.resolve(
                    ConfigKey.PIPELINE_QUALIFIED_STAGE_ID, merchant_id=turn_ctx.merchant_id
                )
                if not stage_id:
                    outcome = MoveOutcome(False, None, None, "no_stage_configured")
                else:
                    # Opportunity + pipeline ids: prefer the action payload,
                    # fall back to whatever the booking handler stamped on
                    # `lead.meta`, and finally to the merchant's configured
                    # default pipeline. This is what unblocks UC-04 — the
                    # orchestrator never knows the GHL ids; the booking
                    # handler does.
                    payload_opp = action.payload.get("opportunity_id")
                    payload_pipe = action.payload.get("pipeline_id")
                    lead_row = await leads.get_by_phone(
                        merchant_id=turn_ctx.merchant_id, phone=turn_ctx.lead_phone
                    )
                    lead_meta = dict(lead_row.meta or {}) if lead_row else {}
                    opportunity_id = payload_opp or lead_meta.get("ghl_opportunity_id")
                    pipeline_id = (
                        payload_pipe
                        or lead_meta.get("ghl_pipeline_id")
                        or await config.resolve(
                            ConfigKey.PIPELINE_DEFAULT_PIPELINE_ID,
                            merchant_id=turn_ctx.merchant_id,
                        )
                    )

                    async def _persist_tokens(bundle: GHLTokenBundle) -> None:
                        # Own committed transaction — see booking.py: a later GHL
                        # error rolls back the handler session, but the rotated
                        # refresh token must survive (GHL already invalidated the
                        # old one).
                        async with session_scope() as token_session:
                            await IntegrationRepository(
                                token_session, kek_base64=self._kek
                            ).upsert_ghl(
                                merchant_id=turn_ctx.merchant_id,
                                access_token=bundle.access_token,
                                refresh_token=bundle.refresh_token,
                                expires_at=bundle.expires_at,
                                location_id=bundle.location_id,
                            )

                    outcome = await self._execute(
                        ghl=ghl,
                        stage_id=str(stage_id),
                        pipeline_id=str(pipeline_id) if pipeline_id else None,
                        opportunity_id=str(opportunity_id) if opportunity_id else None,
                        contact_phone=turn_ctx.lead_phone,
                        contact_fields=action.payload.get("contact_fields", {}),
                        value=action.payload.get("value"),
                        currency=action.payload.get("currency", "EUR"),
                        on_token_refresh=_persist_tokens,
                    )

            if outcome.moved and outcome.stage_id:
                lead = await leads.get_by_phone(
                    merchant_id=turn_ctx.merchant_id, phone=turn_ctx.lead_phone
                )
                if lead is not None:
                    lead.pipeline_stage_id = outcome.stage_id
                    if outcome.opportunity_id:
                        lead.meta = {
                            **(lead.meta or {}),
                            "ghl_opportunity_id": outcome.opportunity_id,
                        }

            await analytics.emit(
                tenant_id=turn_ctx.tenant_id,
                merchant_id=turn_ctx.merchant_id,
                event_type="pipeline.moved" if outcome.moved else "pipeline.failed",
                subject_type="lead",
                subject_id=turn_ctx.lead_id,
                properties={
                    "stage_id": outcome.stage_id,
                    "opportunity_id": outcome.opportunity_id,
                    "reason": outcome.reason,
                    "conversation_id": str(turn_ctx.conversation_id),
                    "llm_reason": action.payload.get("reason"),
                },
            )

    async def _execute(
        self,
        *,
        ghl,
        stage_id: str,
        pipeline_id: str | None,
        opportunity_id: str | None,
        contact_phone: str,
        contact_fields: dict[str, Any],
        value: float | None,
        currency: str,
        on_token_refresh=None,
    ) -> MoveOutcome:
        client = GHLClient(
            token_bundle=GHLTokenBundle(
                access_token=ghl.access_token,
                refresh_token=ghl.refresh_token,
                expires_at=ghl.expires_at,
                location_id=ghl.location_id,
            ),
            client_id=self._client_id,
            client_secret=self._client_secret,
            on_token_refresh=on_token_refresh,
        )
        try:
            contact = await client.upsert_contact(
                {
                    "phone": contact_phone,
                    "email": contact_fields.get("email"),
                    "firstName": contact_fields.get("first_name") or contact_fields.get("name"),
                    "lastName": contact_fields.get("last_name"),
                }
            )
            contact_id = contact.get("contact", {}).get("id") or contact.get("id")
            if not contact_id:
                return MoveOutcome(False, None, None, "contact_upsert_failed")

            if opportunity_id is None:
                # No opportunity yet — the lead never went through book_slot (which
                # stamps the opportunity on lead.meta). Create one directly in the
                # target stage: creating it there *is* the move. Needs a pipeline
                # and a location to anchor it.
                if pipeline_id is None or not ghl.location_id:
                    return MoveOutcome(False, stage_id, None, "no_opportunity_or_pipeline")
                name = (
                    contact_fields.get("name")
                    or contact_fields.get("first_name")
                    or "Lead WhatsApp"
                )
                try:
                    created = await client.create_opportunity(
                        pipeline_id=pipeline_id,
                        stage_id=stage_id,
                        contact_id=contact_id,
                        location_id=ghl.location_id,
                        name=str(name),
                        monetary_value=value,
                    )
                except IntegrationError as e:
                    logger.warning("move_pipeline.create_failed", error=str(e))
                    return MoveOutcome(False, stage_id, None, "opportunity_create_failed")
                created_id = created.get("id") or created.get("opportunity", {}).get("id")
                if not isinstance(created_id, str):
                    return MoveOutcome(False, stage_id, None, "opportunity_create_failed")
                return MoveOutcome(True, stage_id, created_id, "created_in_stage")

            try:
                await client.move_opportunity(
                    opportunity_id, stage_id=stage_id, pipeline_id=pipeline_id or ""
                )
                return MoveOutcome(True, stage_id, opportunity_id, "moved")
            except IntegrationError as e:
                logger.warning(
                    "move_pipeline.ghl_failed",
                    error=str(e),
                    opportunity_id=opportunity_id,
                    stage_id=stage_id,
                )
                return MoveOutcome(False, stage_id, opportunity_id, "ghl_move_failed")
        finally:
            await client.close()
