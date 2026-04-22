"""initial schema — tenants, merchants, users, bot config, kb, conversations, leads, ab, analytics, integrations, ft

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-21

Creates the full V1 schema including pgvector extension, HNSW index on
kb_chunks.embedding, and RLS policies keyed on Supabase JWT claims
(current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id' / 'merchant_id').
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES_TENANT_SCOPED = (
    "tenants",
    "bot_templates",
    "ft_models",
    "analytics_events",
)

RLS_TABLES_MERCHANT_SCOPED = (
    "merchants",
    "users",
    "bot_configs",
    "prompt_templates",
    "knowledge_base_docs",
    "kb_chunks",
    "conversations",
    "messages",
    "leads",
    "objections",
    "ab_experiments",
    "ab_assignments",
    "integrations",
)


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA extensions')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector" WITH SCHEMA extensions')

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "merchants",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Rome"),
        sa.Column("locale", sa.String(8), nullable=False, server_default="it"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_merchants_tenant_slug"),
    )
    op.create_index("ix_merchants_tenant_id", "merchants", ["tenant_id"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_merchant_id", "users", ["merchant_id"])

    op.create_table(
        "bot_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("defaults", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("locked_keys", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_bot_templates_tenant_name"),
    )
    op.create_index("ix_bot_templates_tenant_id", "bot_templates", ["tenant_id"])

    op.create_table(
        "bot_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bot_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("overrides", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("variant_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("merchant_id", "kind", "version", "variant_id", name="uq_prompt_templates_version_variant"),
    )
    op.create_index("ix_prompt_templates_merchant_id", "prompt_templates", ["merchant_id"])

    op.create_table(
        "knowledge_base_docs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("storage_path", sa.String(500)),
        sa.Column("url", sa.String(2000)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_knowledge_base_docs_merchant_id", "knowledge_base_docs", ["merchant_id"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("doc_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_base_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_chunks_doc_id", "kb_chunks", ["doc_id"])
    op.create_index("ix_kb_chunks_merchant_id", "kb_chunks", ["merchant_id"])
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("ghl_contact_id", sa.String(120)),
        sa.Column("name", sa.String(200)),
        sa.Column("email", sa.String(320)),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score_reasons", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sentiment", sa.String(16)),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("last_interaction_at", sa.String(64)),
        sa.Column("pipeline_stage_id", sa.String(120)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("merchant_id", "phone", name="uq_leads_merchant_phone"),
        sa.UniqueConstraint("merchant_id", "ghl_contact_id", name="uq_leads_merchant_ghl"),
    )
    op.create_index("ix_leads_merchant_id", "leads", ["merchant_id"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("wa_phone_number_id", sa.String(64)),
        sa.Column("wa_contact_phone", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("variant_id", sa.String(32)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversations_merchant_id", "conversations", ["merchant_id"])
    op.create_index("ix_conversations_lead_id", "conversations", ["lead_id"])
    op.create_index("ix_conversations_wa_contact_phone", "conversations", ["wa_contact_phone"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("variant_id", sa.String(32)),
        sa.Column("model", sa.String(120)),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("wa_message_id", sa.String(120)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_merchant_id", "messages", ["merchant_id"])
    op.create_index("ix_messages_wa_message_id", "messages", ["wa_message_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    op.create_table(
        "objections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("quote", sa.String(2000)),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_objections_merchant_id", "objections", ["merchant_id"])
    op.create_index("ix_objections_conversation_id", "objections", ["conversation_id"])
    op.create_index("ix_objections_category", "objections", ["category"])

    op.create_table(
        "ab_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("variants", postgresql.JSONB, nullable=False),
        sa.Column("primary_metric", sa.String(64), nullable=False),
        sa.Column("min_sample_size", sa.Integer, nullable=False, server_default="100"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ab_experiments_merchant_id", "ab_experiments", ["merchant_id"])

    op.create_table(
        "ab_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ab_experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", sa.String(32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("experiment_id", "lead_id", name="uq_ab_assignments_exp_lead"),
    )
    op.create_index("ix_ab_assignments_experiment_id", "ab_assignments", ["experiment_id"])
    op.create_index("ix_ab_assignments_merchant_id", "ab_assignments", ["merchant_id"])
    op.create_index("ix_ab_assignments_lead_id", "ab_assignments", ["lead_id"])

    op.create_table(
        "analytics_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(32)),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True)),
        sa.Column("variant_id", sa.String(32)),
        sa.Column("properties", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_analytics_events_tenant_id", "analytics_events", ["tenant_id"])
    op.create_index("ix_analytics_events_merchant_id", "analytics_events", ["merchant_id"])
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])
    op.create_index("ix_analytics_events_occurred_at", "analytics_events", ["occurred_at"])

    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("external_account_id", sa.String(200)),
        sa.Column("secret_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary, nullable=False),
        sa.Column("secret_aad", sa.LargeBinary),
        sa.Column("kek_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("merchant_id", "provider", name="uq_integrations_merchant_provider"),
    )
    op.create_index("ix_integrations_merchant_id", "integrations", ["merchant_id"])

    op.create_table(
        "ft_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("base_model", sa.String(120), nullable=False),
        sa.Column("provider_model_id", sa.String(200), nullable=False),
        sa.Column("dataset_path", sa.String(500)),
        sa.Column("training_job_id", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("trained_at", sa.DateTime(timezone=True)),
        sa.Column("evaluation", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ft_models_tenant_id", "ft_models", ["tenant_id"])
    op.create_index("ix_ft_models_merchant_id", "ft_models", ["merchant_id"])

    # ---- Row-Level Security ----
    # Policies read from current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id' / 'merchant_id'. On Supabase this
    # function is provided; for forged backend sessions we SET LOCAL "request.jwt.claims"
    # in db.session.tenant_session() so the same expressions evaluate correctly.
    for table in (*RLS_TABLES_TENANT_SCOPED, *RLS_TABLES_MERCHANT_SCOPED):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Tenant-scoped: one tenant sees its own rows only.
    for table in RLS_TABLES_TENANT_SCOPED:
        col = "id" if table == "tenants" else "tenant_id"
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING ((current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = {col})
            WITH CHECK ((current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = {col})
            """
        )

    # Merchant-scoped tables: the tenant boundary is enforced via a JOIN through
    # merchants.tenant_id (none of these tables carry tenant_id directly). An agency
    # user (no merchant_id claim) can see all rows belonging to merchants of their
    # tenant; a merchant user only sees rows for their own merchant.
    #
    # Future optimisation: denormalise tenant_id onto every merchant-scoped table and
    # replace the EXISTS with a direct column match. See docs/decisions/.
    def _merchant_scoped_predicate(table: str) -> str:
        merchant_col = "id" if table == "merchants" else "merchant_id"
        return f"""
            EXISTS (
                SELECT 1 FROM merchants m
                WHERE m.id = {table}.{merchant_col}
                  AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
                  AND (
                      (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                      OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
                  )
            )
        """

    # users has its own tenant_id column — validate directly and by merchant claim.
    op.execute(
        """
        CREATE POLICY tenant_or_merchant_isolation_users ON users
        USING (
            (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = tenant_id
            AND (
                (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                OR merchant_id IS NULL
                OR (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid = merchant_id
            )
        )
        WITH CHECK (
            (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid = tenant_id
            AND (
                (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                OR merchant_id IS NULL
                OR (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid = merchant_id
            )
        )
        """
    )

    for table in RLS_TABLES_MERCHANT_SCOPED:
        if table == "users":
            continue
        predicate = _merchant_scoped_predicate(table)
        op.execute(
            f"""
            CREATE POLICY merchant_isolation_{table} ON {table}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    tables_in_reverse = (
        "ft_models",
        "integrations",
        "analytics_events",
        "ab_assignments",
        "ab_experiments",
        "objections",
        "messages",
        "conversations",
        "leads",
        "kb_chunks",
        "knowledge_base_docs",
        "prompt_templates",
        "bot_configs",
        "bot_templates",
        "users",
        "merchants",
        "tenants",
    )
    for table in tables_in_reverse:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute('DROP EXTENSION IF EXISTS "vector"')
    # keep pgcrypto; it's inexpensive and often needed elsewhere
