"""automation_hours_queue — i rami di automazione sospesi fuori orario

Revision ID: 0051_automation_hours_queue
Revises: 0050_response_schedule
Create Date: 2026-09-01

ADR 0030. Da 0050 il vincolo `schedule.*` valeva solo per il turno in ingresso:
gli invii proattivi della lavagnetta partivano a qualunque ora. Con
`schedule.apply_to_automations` acceso il walk si ferma sul nodo customer-facing
e lascia una riga qui; lo sweep `flush_automation_hours_queue` (ogni 5 minuti)
la ripesca alla riapertura e riaccoda `automation_run` con
`start_keys = node_keys`.

La riga è un **puntatore**, non un messaggio: nessun testo. Il contenuto viene
ricalcolato dalla lavagnetta alla consegna (ADR 0014), la finestra 24h viene
rivalutata su stato fresco e la guardia d'episodio di ADR 0015 riparte.

Perché una tabella e non un job arq differito (le tre ragioni di ADR 0028 §2,
qui quasi identiche): l'attesa dura da una notte a un fine settimana lungo e in
Redis non sopravvive a un riavvio; il momento dell'apertura **cambia** e un job
accodato porta con sé un orario deciso ieri; gli id job stabili sono la trappola
nota di arq. In più una tabella è ispezionabile — "quanti messaggi ha in coda
questo merchant" a un job in Redis non si può chiedere.

RLS: la stessa merchant-scoped di 0027 per le altre `automation_*` (EXISTS
attraverso `merchants.tenant_id`). `tenant_id` è denormalizzato in colonna
perché lo sweep gira cross-tenant e ricostruisce il TenantContext senza join,
ma NON è il predicato di isolamento: quello resta l'EXISTS su `merchants`, così
la tabella si comporta come le sue sorelle e non introduce una seconda forma da
tenere allineata.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_automation_hours_queue"
down_revision: str | Sequence[str] | None = "0050_response_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "automation_hours_queue"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "node_keys",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("episode_anchor", sa.String(64)),
        sa.Column("dedup_key", sa.Text, nullable=False),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])
    op.create_index(f"ix_{_TABLE}_merchant_id", _TABLE, ["merchant_id"])
    op.create_index(f"ix_{_TABLE}_automation_id", _TABLE, ["automation_id"])
    # Lo sweep scansiona in ordine di attesa: i più vecchi per primi, così se il
    # cap tronca a restare indietro è chi ha aspettato meno.
    op.create_index(f"ix_{_TABLE}_queued_at", _TABLE, ["queued_at"])
    # Idempotenza dell'accodamento: la ri-scansione del dispatcher entro i 120s
    # di lookback (o una ri-consegna arq) ritrova la riga invece di duplicarla.
    op.create_unique_constraint(f"uq_{_TABLE}_dedup", _TABLE, ["dedup_key"])

    # ---- Row-Level Security (identica a 0027 per le altre automation_*) ----
    predicate = f"""
        EXISTS (
            SELECT 1 FROM merchants m
            WHERE m.id = {_TABLE}.merchant_id
              AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
              AND (
                  (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                  OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
              )
        )
    """
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_{_TABLE} ON {_TABLE}
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS merchant_isolation_{_TABLE} ON {_TABLE}")
    op.drop_index(f"ix_{_TABLE}_queued_at", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_automation_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_merchant_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
