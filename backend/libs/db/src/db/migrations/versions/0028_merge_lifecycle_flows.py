"""merge legacy linear flows into the automation graph (system_key + data)

Revision ID: 0028_merge_lifecycle_flows
Revises: 0027_automation_flows
Create Date: 2026-06-19

Unifies the two automation systems. The 4 legacy lifecycle flows (`flows` /
`flow_steps`) become **system** automations on the same graph model the canvas
edits:

  * `automation_flows.system_key` (nullable) tags the 4 lifecycle flows. Partial
    unique per merchant. NULL for custom (event-driven) flows.
  * Data conversion: every `flows` row + its ordered `flow_steps` becomes one
    `automation_flows` (system_key=key, trigger_type mapped) with a locked trigger
    node and a linear chain of `wait`(if delay>0)/`send` nodes + edges. Each step
    maps to one `send` node carrying {window_policy, free_text, template_id,
    variable_mapping} — the full ResolvedFlowStep surface. Step order (= attempt
    index) is preserved exactly; ALL steps are converted (a rare per-step
    `enabled=false` becomes an active node the merchant can delete on the canvas).

The legacy `flows`/`flow_steps` tables are KEPT (deprecated) — they're dropped in
a follow-up once the FE has fully cut over. Schedulers now resolve their step by
walking the system automation graph; `decide_outbound` is untouched.

RLS note: `flows`/`flow_steps` AND `automation_*` are all FORCE ROW LEVEL
SECURITY, so even the table owner is subject to the policy. The migration has no
JWT claims set, so a plain SELECT on `flows` would see ZERO rows (policy fails
closed) — converting nothing. We therefore temporarily drop FORCE on all five
tables around the conversion (owner bypasses RLS when not forced, regardless of
whether the migration role also has BYPASSRLS), then restore it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_merge_lifecycle_flows"
down_revision: str | Sequence[str] | None = "0027_automation_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUTOMATION_TABLES = ("automation_flows", "automation_nodes", "automation_edges")
# Both the read side (flows/flow_steps) and write side (automation_*) are FORCE
# RLS; drop FORCE on all of them around the conversion so it can run JWT-less.
_CONVERT_RLS_TABLES = ("flows", "flow_steps", *_AUTOMATION_TABLES)

_CONVERT_SQL = """
DO $$
DECLARE
    f RECORD;
    s RECORD;
    new_flow_id uuid;
    trig_type text;
    prev_key text;
    idx int;
    send_key text;
    wait_key text;
BEGIN
    FOR f IN SELECT * FROM flows LOOP
        trig_type := CASE f.key
            WHEN 'no_answer' THEN 'no_answer'
            WHEN 'reactivation' THEN 'lead_dormant'
            WHEN 'booking_reminder' THEN 'booking_created'
            WHEN 'first_contact' THEN 'message_received'
            ELSE 'message_received'
        END;
        new_flow_id := gen_random_uuid();
        INSERT INTO automation_flows
            (id, merchant_id, name, description, enabled, system_key, trigger_type,
             trigger_config, canvas, created_at, updated_at)
        VALUES
            (new_flow_id, f.merchant_id, f.name, NULL, f.enabled, f.key, trig_type,
             '{}'::jsonb, '{}'::jsonb, now(), now());

        INSERT INTO automation_nodes
            (id, automation_id, merchant_id, node_key, kind, type, config,
             position_x, position_y, created_at, updated_at)
        VALUES
            (gen_random_uuid(), new_flow_id, f.merchant_id, 't', 'trigger', trig_type,
             '{}'::jsonb, 160, 60, now(), now());

        prev_key := 't';
        idx := 0;
        FOR s IN SELECT * FROM flow_steps WHERE flow_id = f.id ORDER BY step_index LOOP
            IF COALESCE(s.delay_minutes, 0) > 0 THEN
                wait_key := 'w' || idx;
                INSERT INTO automation_nodes
                    (id, automation_id, merchant_id, node_key, kind, type, config,
                     position_x, position_y, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), new_flow_id, f.merchant_id, wait_key, 'action', 'wait',
                     jsonb_build_object('minutes', s.delay_minutes),
                     160, 140 + idx * 160, now(), now());
                INSERT INTO automation_edges
                    (id, automation_id, merchant_id, source_key, target_key, branch,
                     created_at, updated_at)
                VALUES
                    (gen_random_uuid(), new_flow_id, f.merchant_id, prev_key, wait_key,
                     'default', now(), now());
                prev_key := wait_key;
            END IF;

            send_key := 's' || idx;
            INSERT INTO automation_nodes
                (id, automation_id, merchant_id, node_key, kind, type, config,
                 position_x, position_y, created_at, updated_at)
            VALUES
                (gen_random_uuid(), new_flow_id, f.merchant_id, send_key, 'action', 'send',
                 jsonb_build_object(
                     'window_policy', s.window_policy,
                     'free_text', s.free_text,
                     'template_id', s.template_id,
                     'variable_mapping', COALESCE(s.variable_mapping, '{}'::jsonb)
                 ),
                 160, 220 + idx * 160, now(), now());
            INSERT INTO automation_edges
                (id, automation_id, merchant_id, source_key, target_key, branch,
                 created_at, updated_at)
            VALUES
                (gen_random_uuid(), new_flow_id, f.merchant_id, prev_key, send_key,
                 'default', now(), now());
            prev_key := send_key;
            idx := idx + 1;
        END LOOP;
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.add_column("automation_flows", sa.Column("system_key", sa.String(32), nullable=True))
    op.create_index("ix_automation_flows_system_key", "automation_flows", ["system_key"])
    op.execute(
        "CREATE UNIQUE INDEX uq_automation_flows_system_key "
        "ON automation_flows (merchant_id, system_key) WHERE system_key IS NOT NULL"
    )

    # Conversion reads flows/flow_steps and writes automation_* — all FORCE RLS.
    # Drop FORCE so the owner can read+write with no JWT context, then restore.
    for table in _CONVERT_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(_CONVERT_SQL)
    for table in _CONVERT_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Remove converted system flows before dropping the column (CASCADE clears
    # their nodes/edges); toggle FORCE off so the owner can delete without JWT.
    for table in _AUTOMATION_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM automation_flows WHERE system_key IS NOT NULL")
    for table in _AUTOMATION_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS uq_automation_flows_system_key")
    op.drop_index("ix_automation_flows_system_key", table_name="automation_flows")
    op.drop_column("automation_flows", "system_key")
