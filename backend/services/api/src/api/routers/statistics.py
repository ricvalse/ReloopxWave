"""Statistiche configurabili: profili di conversazione ed esiti tracciabili.

Due CRUD che alimentano la stessa pagina:

- **profili** (`/statistics/profiles`, ADR 0022) — l'asse su cui la pagina si
  divide. Ogni profilo può avere il proprio set di bolle, perché
  `dashboard.metrics` è una chiave del config cascade e il profilo ne è il
  livello 0.
- **esiti** (`/statistics/outcomes`) — il vocabolario delle bolle *custom*. È
  ciò che il merchant "crea prima e cabla poi" in un nodo `emit_outcome`: la
  tendina del nodo legge questo elenco, così un esito non può essere una stringa
  digitata a mano che nessuno confronterà mai con quella scritta altrove.

Le bolle **strutturali** (messaggi inviati, risposte ricevute) non passano di
qui: escono dalle colonne di `messages` e non richiedono né dichiarazione né
automazione. La distinzione va mantenuta anche in UI, altrimenti il merchant
cerca dove "cablare" i messaggi inviati e non lo trova.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.dependencies.session import CurrentContext, DBSession
from config_resolver import BotConfigSchema
from db import ConversationProfileRepository, OutcomeRepository
from db.models.outcome import OUTCOME_CARDINALITIES, OUTCOME_SOURCES
from shared import NotFoundError, PermissionDeniedError, get_logger

router = APIRouter()
logger = get_logger(__name__)


def _require_merchant_scope(ctx: CurrentContext) -> UUID:
    if ctx.merchant_id is None:
        raise PermissionDeniedError(
            "Merchant scope required",
            role=ctx.role,
        )
    return ctx.merchant_id


# ---- Profili ---------------------------------------------------------------


class ProfileUpsertIn(BaseModel):
    key: str = Field(max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool = False
    overrides: dict[str, Any] = Field(default_factory=dict)


class ProfilePatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    overrides: dict[str, Any] | None = None
    is_default: bool | None = None


class ProfileOut(BaseModel):
    id: UUID
    key: str
    name: str
    description: str | None
    is_default: bool
    enabled: bool
    # True quando la riga è di libreria d'agenzia: il merchant la vede e la può
    # usare, ma non modificarla.
    is_library: bool
    overrides: dict[str, Any]


def _profile_out(row: Any) -> ProfileOut:
    return ProfileOut(
        id=row.id,
        key=row.key,
        name=row.name,
        description=row.description,
        is_default=row.is_default,
        enabled=row.enabled,
        is_library=row.merchant_id is None,
        overrides=dict(row.overrides or {}),
    )


def _validate_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Gli override di un profilo hanno la stessa forma della config del merchant.

    Validarli qui evita che un profilo scriva una chiave inesistente che poi il
    resolver ignorerebbe in silenzio — cioè un profilo che "non fa niente" senza
    che nessuno capisca perché.
    """
    try:
        BotConfigSchema.model_validate(overrides)
    except Exception as e:  # pydantic ValidationError
        raise HTTPException(status_code=422, detail=f"overrides non validi: {e}") from e
    return overrides


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(ctx: CurrentContext, session: DBSession) -> list[ProfileOut]:
    repo = ConversationProfileRepository(session)
    rows = await repo.list_visible(tenant_id=ctx.tenant_id, merchant_id=ctx.merchant_id)
    return [_profile_out(r) for r in rows]


@router.post("/profiles", response_model=ProfileOut, status_code=201)
async def create_profile(
    payload: ProfileUpsertIn, ctx: CurrentContext, session: DBSession
) -> ProfileOut:
    # Un utente d'agenzia (senza claim merchant) crea righe di libreria; un
    # utente merchant crea righe proprie. È la stessa asimmetria della policy RLS.
    merchant_id = ctx.merchant_id
    repo = ConversationProfileRepository(session)
    if merchant_id is not None and await repo.get_by_key(merchant_id=merchant_id, key=payload.key):
        raise HTTPException(
            status_code=422, detail=f"esiste già un profilo con key {payload.key!r}"
        )

    row = await repo.create(
        tenant_id=ctx.tenant_id,
        merchant_id=merchant_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        overrides=_validate_overrides(payload.overrides),
        is_default=payload.is_default,
    )
    logger.info("statistics.profile.created", profile=row.key, merchant_id=str(merchant_id))
    return _profile_out(row)


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: UUID, payload: ProfilePatchIn, ctx: CurrentContext, session: DBSession
) -> ProfileOut:
    repo = ConversationProfileRepository(session)
    row = await repo.get(profile_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise NotFoundError("Profile not found", profile_id=str(profile_id))
    if row.merchant_id is None and ctx.merchant_id is not None:
        raise PermissionDeniedError("Un profilo di libreria si modifica dall'agenzia")
    if row.merchant_id is not None and ctx.merchant_id not in (None, row.merchant_id):
        raise PermissionDeniedError("Profilo di un altro merchant")

    updated = await repo.update_fields(
        profile_id,
        name=payload.name,
        description=payload.description,
        overrides=(
            _validate_overrides(payload.overrides) if payload.overrides is not None else None
        ),
        enabled=payload.enabled,
    )
    if updated is None:
        raise NotFoundError("Profile not found", profile_id=str(profile_id))
    if payload.is_default and updated.merchant_id is not None:
        await repo.set_default(merchant_id=updated.merchant_id, profile_id=profile_id)
        updated = await repo.get(profile_id) or updated

    # La cache va invalidata SENZA elenco di chiavi: la forma mirata non tocca le
    # voci namespacate per profilo (`cfg:{merchant}:p:{profilo}:*`).
    if updated.merchant_id is not None:
        from config_resolver import ConfigResolver

        await ConfigResolver(session).invalidate(updated.merchant_id)
    return _profile_out(updated)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: UUID, ctx: CurrentContext, session: DBSession) -> None:
    repo = ConversationProfileRepository(session)
    row = await repo.get(profile_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise NotFoundError("Profile not found", profile_id=str(profile_id))
    if row.merchant_id is None and ctx.merchant_id is not None:
        raise PermissionDeniedError("Un profilo di libreria si elimina dall'agenzia")
    if row.is_default:
        raise HTTPException(
            status_code=422,
            detail="Non si può eliminare il profilo di default: assegnane un altro prima.",
        )
    await repo.delete(profile_id)
    if row.merchant_id is not None:
        from config_resolver import ConfigResolver

        await ConfigResolver(session).invalidate(row.merchant_id)


# ---- Esiti (le statistiche custom) -----------------------------------------


class OutcomeUpsertIn(BaseModel):
    key: str = Field(max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    source_kind: str = Field(default="ai_check", max_length=24)
    cardinality: str = Field(default="once_per_lead", max_length=24)


class OutcomePatchIn(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    source_kind: str | None = Field(default=None, max_length=24)
    enabled: bool | None = None


class OutcomeOut(BaseModel):
    id: UUID
    key: str
    label: str
    description: str | None
    source_kind: str
    cardinality: str
    enabled: bool
    is_library: bool


def _outcome_out(row: Any) -> OutcomeOut:
    return OutcomeOut(
        id=row.id,
        key=row.key,
        label=row.label,
        description=row.description,
        source_kind=row.source_kind,
        cardinality=row.cardinality,
        enabled=row.enabled,
        is_library=row.merchant_id is None,
    )


@router.get("/outcomes", response_model=list[OutcomeOut])
async def list_outcomes(
    ctx: CurrentContext, session: DBSession, enabled_only: bool = False
) -> list[OutcomeOut]:
    """Il vocabolario degli esiti di questo merchant.

    È la tendina del metric-builder (accanto al catalogo eventi tipato) **e**
    quella del nodo `emit_outcome`. Le due liste vengono dalla stessa fonte, che
    è esattamente ciò che impedisce a emettitore e lettore di divergere.
    """
    repo = OutcomeRepository(session)
    rows = await repo.list_definitions(
        tenant_id=ctx.tenant_id, merchant_id=ctx.merchant_id, enabled_only=enabled_only
    )
    return [_outcome_out(r) for r in rows]


@router.post("/outcomes", response_model=OutcomeOut, status_code=201)
async def create_outcome(
    payload: OutcomeUpsertIn, ctx: CurrentContext, session: DBSession
) -> OutcomeOut:
    if payload.source_kind not in OUTCOME_SOURCES:
        raise HTTPException(
            status_code=422, detail=f"source_kind non valido: {payload.source_kind!r}"
        )
    if payload.cardinality not in OUTCOME_CARDINALITIES:
        raise HTTPException(
            status_code=422, detail=f"cardinality non valida: {payload.cardinality!r}"
        )

    repo = OutcomeRepository(session)
    merchant_id = ctx.merchant_id
    if merchant_id is not None and await repo.get_definition_by_key(
        merchant_id=merchant_id, key=payload.key
    ):
        raise HTTPException(
            status_code=422, detail=f"esiste già una statistica con key {payload.key!r}"
        )

    row = await repo.create_definition(
        tenant_id=ctx.tenant_id,
        merchant_id=merchant_id,
        key=payload.key,
        label=payload.label,
        description=payload.description,
        source_kind=payload.source_kind,
        cardinality=payload.cardinality,
    )
    logger.info("statistics.outcome.created", outcome=row.key, merchant_id=str(merchant_id))
    return _outcome_out(row)


@router.patch("/outcomes/{outcome_id}", response_model=OutcomeOut)
async def update_outcome(
    outcome_id: UUID, payload: OutcomePatchIn, ctx: CurrentContext, session: DBSession
) -> OutcomeOut:
    """`key` e `cardinality` non sono modificabili — vedi il repository."""
    if payload.source_kind is not None and payload.source_kind not in OUTCOME_SOURCES:
        raise HTTPException(
            status_code=422, detail=f"source_kind non valido: {payload.source_kind!r}"
        )

    repo = OutcomeRepository(session)
    row = await repo.get_definition(outcome_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise NotFoundError("Outcome not found", outcome_id=str(outcome_id))
    if row.merchant_id is None and ctx.merchant_id is not None:
        raise PermissionDeniedError("Una statistica di libreria si modifica dall'agenzia")

    updated = await repo.update_definition(
        outcome_id,
        label=payload.label,
        description=payload.description,
        source_kind=payload.source_kind,
        enabled=payload.enabled,
    )
    if updated is None:
        raise NotFoundError("Outcome not found", outcome_id=str(outcome_id))
    return _outcome_out(updated)


@router.delete("/outcomes/{outcome_id}", status_code=204)
async def delete_outcome(outcome_id: UUID, ctx: CurrentContext, session: DBSession) -> None:
    """Elimina la statistica **e il suo storico** (CASCADE).

    Per smettere di misurare conservando i dati si usa `enabled=false`.
    """
    repo = OutcomeRepository(session)
    row = await repo.get_definition(outcome_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise NotFoundError("Outcome not found", outcome_id=str(outcome_id))
    if row.merchant_id is None and ctx.merchant_id is not None:
        raise PermissionDeniedError("Una statistica di libreria si elimina dall'agenzia")
    await repo.delete_definition(outcome_id)
    logger.info("statistics.outcome.deleted", outcome=row.key)
