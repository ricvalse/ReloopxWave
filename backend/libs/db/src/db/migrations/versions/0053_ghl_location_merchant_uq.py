"""ghl_location_tokens: un merchant, al massimo una location attiva

Revision ID: 0053_ghl_location_merchant_uq
Revises: 0052_last_automation_at
Create Date: 2026-09-18

`merchant_id` non aveva nessun vincolo di unicità (solo `location_id` ne ha
uno) — un admin che ri-collegava un merchant a una nuova location GHL
aggiungeva una seconda riga attiva invece di sostituire la prima, e nulla lo
impediva. Non è un duplicato innocuo: `resolve_location_by_merchant`
(GHLMarketplaceRepository) filtra solo su `merchant_id` e chiama
`scalar_one_or_none()`, che solleva `MultipleResultsFound` su una seconda
riga attiva — rompendo prenotazioni, spostamenti di pipeline e liste
calendari/pipeline per quel merchant, non limitandosi a scegliere quella
sbagliata. `link_location` ora sgancia la location precedente prima di
collegare la nuova (stesso commit); questo indice è la rete di sicurezza a
livello DB per qualunque altro percorso di scrittura, presente o futuro.

Parziale (`WHERE status = 'active'`) e non un vincolo pieno sulla colonna:
righe `pending_link`/`revoked`/`error` con lo stesso `merchant_id` storico
restano legittime (uno storico di re-installazioni), è solo lo stato
`active` a dover essere unico per merchant.

Verificato prima di scrivere la migrazione: zero righe duplicate in
produzione (`SELECT merchant_id, count(*) ... WHERE status='active' GROUP BY
merchant_id HAVING count(*) > 1` → vuoto), quindi l'indice si crea senza
bisogno di una pulizia dati preventiva.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_ghl_location_merchant_uq"
down_revision: str | Sequence[str] | None = "0052_last_automation_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ghl_location_tokens"
_INDEX = "uq_ghl_location_tokens_merchant_active"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        _TABLE,
        ["merchant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
