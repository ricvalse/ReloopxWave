from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import get_tenant_context
from db.session import TenantContext, get_engine, tenant_session


async def init_db(dsn: str) -> None:
    get_engine(dsn)


async def get_db_session(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> AsyncIterator[AsyncSession]:
    async with tenant_session(ctx) as session:
        yield session


DBSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentContext = Annotated[TenantContext, Depends(get_tenant_context)]
