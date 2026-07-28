"""Repository dei profili di conversazione (ADR 0022).

Un profilo è visibile a un merchant se è suo (`merchant_id` = il merchant) o se
è di libreria (`merchant_id` NULL, stesso tenant). La stessa asimmetria vale in
scrittura: solo l'agenzia può creare righe di libreria, ed è la policy RLS di
0047 a imporlo — qui i metodi la rispecchiano per dare errori chiari invece di
lasciar fallire il vincolo.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ConversationProfile


class ConversationProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_visible(
        self, *, tenant_id: UUID, merchant_id: UUID | None
    ) -> list[ConversationProfile]:
        """Profili del merchant + libreria d'agenzia, ordinati con il default in testa."""
        stmt = select(ConversationProfile).where(ConversationProfile.tenant_id == tenant_id)
        if merchant_id is not None:
            stmt = stmt.where(
                or_(
                    ConversationProfile.merchant_id == merchant_id,
                    ConversationProfile.merchant_id.is_(None),
                )
            )
        stmt = stmt.order_by(
            ConversationProfile.is_default.desc(),
            ConversationProfile.name,
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, profile_id: UUID) -> ConversationProfile | None:
        stmt = select(ConversationProfile).where(ConversationProfile.id == profile_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def get_by_key(self, *, merchant_id: UUID, key: str) -> ConversationProfile | None:
        stmt = (
            select(ConversationProfile)
            .where(
                ConversationProfile.merchant_id == merchant_id,
                ConversationProfile.key == key,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def get_default(self, merchant_id: UUID) -> ConversationProfile | None:
        """Il profilo a cui una conversazione torna a fine episodio.

        Al massimo uno per merchant (indice unique parziale in 0047); None se il
        merchant non ne ha ancora definiti — nel qual caso la conversazione gira
        senza profilo, cioè esattamente come prima di ADR 0022.
        """
        stmt = (
            select(ConversationProfile)
            .where(
                ConversationProfile.merchant_id == merchant_id,
                ConversationProfile.is_default.is_(True),
                ConversationProfile.enabled.is_(True),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def resolve_overrides(self, profile_id: UUID | None) -> dict[str, Any]:
        """Gli override di un profilo, o `{}` se non esiste / è disabilitato.

        `{}` è il valore che lascia il resolver identico a com'era prima dei
        profili: un profilo cancellato o spento degrada al comportamento del
        merchant invece di rompere il turno.
        """
        if profile_id is None:
            return {}
        profile = await self.get(profile_id)
        if profile is None or not profile.enabled:
            return {}
        return dict(profile.overrides or {})

    async def create(
        self,
        *,
        tenant_id: UUID,
        merchant_id: UUID | None,
        key: str,
        name: str,
        description: str | None = None,
        overrides: dict[str, Any] | None = None,
        is_default: bool = False,
    ) -> ConversationProfile:
        profile = ConversationProfile(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            key=key,
            name=name,
            description=description,
            overrides=overrides or {},
            is_default=False,
        )
        self._session.add(profile)
        await self._session.flush()
        if is_default and merchant_id is not None:
            await self.set_default(merchant_id=merchant_id, profile_id=profile.id)
        return profile

    async def update_fields(
        self,
        profile_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        overrides: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> ConversationProfile | None:
        profile = await self.get(profile_id)
        if profile is None:
            return None
        if name is not None:
            profile.name = name
        if description is not None:
            profile.description = description
        if overrides is not None:
            profile.overrides = overrides
        if enabled is not None:
            profile.enabled = enabled
        await self._session.flush()
        return profile

    async def set_default(self, *, merchant_id: UUID, profile_id: UUID) -> None:
        """Sposta il flag di default. Prima spegne, poi accende.

        L'ordine conta: l'indice unique parziale `uq_conversation_profiles_default`
        rifiuterebbe due righe a `true` nello stesso istante, quindi accendere
        prima di spegnere fallirebbe.
        """
        await self._session.execute(
            update(ConversationProfile)
            .where(
                ConversationProfile.merchant_id == merchant_id,
                ConversationProfile.is_default.is_(True),
            )
            .values(is_default=False)
        )
        await self._session.flush()
        await self._session.execute(
            update(ConversationProfile)
            .where(ConversationProfile.id == profile_id)
            .values(is_default=True)
        )
        await self._session.flush()

    async def delete(self, profile_id: UUID) -> bool:
        profile = await self.get(profile_id)
        if profile is None:
            return False
        await self._session.delete(profile)
        await self._session.flush()
        return True
