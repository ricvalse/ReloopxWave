"""Attribuzione a tre dimensioni + profili di conversazione + esiti tracciabili

Revision ID: 0047_attribution_profiles_outcomes
Revises: 0046_whatsapp_media_bucket
Create Date: 2026-07-28

Tre tabelle nuove e otto colonne che completano un pattern che lo schema già usa
una volta sola.

**Il pattern.** `variant_id` (A/B) vive denormalizzato sulle stesse tre tabelle —
`conversations`, `messages`, `analytics_events` — perché è la "dimensione di
attribuzione": chi/cosa ha determinato quel turno. Profilo e automazione sono la
stessa identica cosa, e finora uno non esisteva e l'altro era previsto come
chiave JSONB. Qui diventano colonne accanto a `variant_id`. È anche ciò che
ADR 0021 §V2 indicava come prerequisito per segmentare le metriche.

**Perché colonne e non properties JSONB.** Il test è "ci farò un WHERE o un
GROUP BY?". Su `automation_id`/`profile_id`/`sender_type` la risposta è sì a ogni
render della pagina Statistiche. Su `meta.template.components` no — infatti resta
dov'è.

**Puntatori vivi vs timbri storici.** `conversations.profile_id` è un puntatore
mutabile (il profilo attivo, che dura fino a fine episodio) e ha una FK. Le
colonne su `messages`/`analytics_events`/`lead_outcomes` sono timbri immutabili
scritti all'INSERT: **niente FK**, di proposito. Un `ON DELETE SET NULL`
riscriverebbe la storia quando il merchant cancella un'automazione, e la
reportistica di ieri cambierebbe da sola. Un uuid orfano è la risposta onesta
("automazione eliminata"), la perdita del dato no.

**Esiti come righe, non come stringhe.** `lead_outcomes.outcome_id` è una FK a
`outcome_definitions`, non una key testuale: è la stessa lezione del catalogo
eventi tipato di ADR 0021 (un `event_type` scritto a mano in un punto e letto in
un altro ha prodotto una KPI ferma a zero per mesi). I due indici unique parziali
su `cardinality` fanno sì che il conteggio sia corretto per costruzione — con
`once_per_lead` un COUNT(*) conta lead distinti senza DISTINCT — e danno
l'idempotenza sotto un motore di automazioni che, essendo stateless (ADR 0015),
ri-esegue lo stesso ramo a ogni inbound.

NOTA OPERATIVA: il backfill di `messages.sender_type` seguito da SET NOT NULL fa
una scansione completa della tabella. Sui volumi attuali è nell'ordine dei
secondi; se `messages` dovesse crescere di un paio di ordini di grandezza, questa
migrazione andrà spezzata (colonna nullable → backfill a lotti → constraint
NOT VALID → VALIDATE).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_attribution_profiles_outcomes"
down_revision: str | Sequence[str] | None = "0046_whatsapp_media_bucket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `meta.sender_type` era un enum di fatto, non tipizzato: il backend ne scriveva
# otto valori da sette file, il union TypeScript del frontend ne dichiarava sei
# (mancavano `customer` e `agent_action`, quindi un messaggio inviato da
# un'azione dell'agente non matchava nessun ramo della UI). Diventa una colonna
# con CHECK, e da lì un enum in OpenAPI che il frontend genera invece di
# riscrivere a mano.
_SENDER_TYPES = (
    "customer",  # inbound dal lead
    "phone",  # echo dall'app WhatsApp del merchant (Coexistence)
    "human",  # operatore dal composer web
    "ai",  # risposta dell'agente sul turno inbound
    "agent_action",  # invio da un'azione dell'agente (es. propose_slots)
    "automation",  # nodo send/send_message/send_template della lavagnetta
    "automation_ai",  # nodo ai_reply della lavagnetta
    "appointment_reminder",  # scheduler promemoria appuntamento
)

# Come è stato accertato QUESTO record di esito. Sta sulla riga e non sulla
# definizione di proposito: permette di partire con un `ai_check` e passare a un
# webhook più avanti senza perdere lo storico né mescolare in silenzio dati di
# qualità diversa (la bolla può dire "312, di cui 180 verificati").
_OUTCOME_SOURCES = ("ai_check", "webhook", "manual", "automation", "agent_action", "api")

# Quante volte lo stesso esito può ripetersi. Denormalizzata su `lead_outcomes`
# perché un indice unique parziale non può leggere una colonna di un'altra
# tabella.
_CARDINALITIES = ("once_per_lead", "once_per_conversation", "repeatable")

_LIBRARY_TABLES = ("conversation_profiles", "outcome_definitions")


def _ts_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
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


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1 · conversation_profiles — le definizioni (ADR 0022)
    # ------------------------------------------------------------------ #
    # `merchant_id` NULL = profilo-libreria d'agenzia, riusabile dai merchant
    # del tenant (stessa ownership dei BotTemplate, ADR 0022 decisione 4).
    op.create_table(
        "conversation_profiles",
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
            nullable=True,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # Stessa shape validata da BotConfigSchema: il profilo è un override-bag
        # DELTA che vince sul config del merchant, non un prompt riscritto da zero
        # (ADR 0022 decisione 1).
        sa.Column(
            "overrides", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_ts_columns(),
    )
    op.create_index(
        "ix_conversation_profiles_merchant_id", "conversation_profiles", ["merchant_id"]
    )
    op.create_index("ix_conversation_profiles_tenant_id", "conversation_profiles", ["tenant_id"])
    # Unicità della key: per-merchant sui profili del merchant, per-tenant su
    # quelli di libreria. Due indici parziali invece di UNIQUE NULLS NOT
    # DISTINCT — semantica più corretta (la libreria è unica nel tenant, non
    # globalmente) e nessun vincolo sulla versione di Postgres.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversation_profiles_merchant_key
        ON conversation_profiles (merchant_id, key) WHERE merchant_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversation_profiles_library_key
        ON conversation_profiles (tenant_id, key) WHERE merchant_id IS NULL
        """
    )
    # Un solo profilo di default per merchant (ADR 0022 decisione 3: a fine
    # episodio la conversazione ci ritorna, quindi deve essere non ambiguo).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversation_profiles_default
        ON conversation_profiles (merchant_id) WHERE is_default AND merchant_id IS NOT NULL
        """
    )

    # ------------------------------------------------------------------ #
    # 2 · outcome_definitions — il vocabolario degli esiti
    # ------------------------------------------------------------------ #
    # `key` è stabile e non si rinomina mai (ci puntano le righe storiche);
    # `label` è quello che il merchant vede e può cambiare a piacere senza
    # orfanare niente.
    op.create_table(
        "outcome_definitions",
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
            nullable=True,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("source_kind", sa.String(24), nullable=False, server_default="ai_check"),
        sa.Column("cardinality", sa.String(24), nullable=False, server_default="once_per_lead"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_ts_columns(),
        sa.CheckConstraint(
            f"source_kind IN ({_in_list(_OUTCOME_SOURCES)})",
            name="ck_outcome_definitions_source_kind",
        ),
        sa.CheckConstraint(
            f"cardinality IN ({_in_list(_CARDINALITIES)})",
            name="ck_outcome_definitions_cardinality",
        ),
    )
    op.create_index("ix_outcome_definitions_merchant_id", "outcome_definitions", ["merchant_id"])
    op.create_index("ix_outcome_definitions_tenant_id", "outcome_definitions", ["tenant_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX uq_outcome_definitions_merchant_key
        ON outcome_definitions (merchant_id, key) WHERE merchant_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_outcome_definitions_library_key
        ON outcome_definitions (tenant_id, key) WHERE merchant_id IS NULL
        """
    )

    # ------------------------------------------------------------------ #
    # 3 · lead_outcomes — i fatti
    # ------------------------------------------------------------------ #
    op.create_table(
        "lead_outcomes",
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
        # FK, non stringa: è il punto di tutto il disegno.
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcome_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Timbri: sotto quale profilo / da quale automazione è maturato l'esito.
        # Nessuna FK — vedi il razionale nel docstring.
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("automation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("automation_node_key", sa.String(64), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        # Valorizzata solo dalle sorgenti inferite (ai_check); NULL per i fatti
        # certi (webhook) e per quelli verificati da un umano.
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column(
            "value", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("cardinality", sa.String(24), nullable=False, server_default="once_per_lead"),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"source IN ({_in_list(_OUTCOME_SOURCES)})", name="ck_lead_outcomes_source"
        ),
        sa.CheckConstraint(
            f"cardinality IN ({_in_list(_CARDINALITIES)})", name="ck_lead_outcomes_cardinality"
        ),
    )
    op.create_index("ix_lead_outcomes_merchant_id", "lead_outcomes", ["merchant_id"])
    op.create_index("ix_lead_outcomes_lead_id", "lead_outcomes", ["lead_id"])
    op.create_index(
        "ix_lead_outcomes_merchant_outcome_occurred",
        "lead_outcomes",
        ["merchant_id", "outcome_id", "occurred_at"],
    )
    # I due vincoli che rendono il conteggio corretto per costruzione e
    # l'emissione idempotente (INSERT ... ON CONFLICT DO NOTHING).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lead_outcomes_per_lead
        ON lead_outcomes (lead_id, outcome_id) WHERE cardinality = 'once_per_lead'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lead_outcomes_per_conversation
        ON lead_outcomes (conversation_id, outcome_id)
        WHERE cardinality = 'once_per_conversation' AND conversation_id IS NOT NULL
        """
    )

    # ------------------------------------------------------------------ #
    # 4 · conversations — il puntatore vivo al profilo attivo
    # ------------------------------------------------------------------ #
    op.add_column(
        "conversations",
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        """
        CREATE INDEX ix_conversations_profile
        ON conversations (merchant_id, profile_id) WHERE profile_id IS NOT NULL
        """
    )

    # ------------------------------------------------------------------ #
    # 5 · messages — provenienza e attribuzione della risposta
    # ------------------------------------------------------------------ #
    op.add_column("messages", sa.Column("sender_type", sa.String(24), nullable=True))
    op.add_column("messages", sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "messages", sa.Column("automation_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("messages", sa.Column("automation_node_key", sa.String(64), nullable=True))
    # Attribuzione conversazionale last-touch: l'ultimo outbound *prima* di
    # questo inbound. NON è il quoted-message di WhatsApp (che, se servirà,
    # andrà in `meta.context`). Scritto una sola volta, sull'inbound.
    op.add_column(
        "messages",
        sa.Column(
            "reply_to_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Backfill dal JSONB, poi il vincolo. `direction` è la fonte di verità per
    # le righe storiche che non hanno mai avuto un sender_type esplicito.
    op.execute(
        """
        UPDATE messages
        SET sender_type = COALESCE(
            NULLIF(meta ->> 'sender_type', ''),
            CASE WHEN direction = 'in' THEN 'customer' ELSE 'ai' END
        )
        WHERE sender_type IS NULL
        """
    )
    # Le righe storiche possono contenere valori fuori dall'enum (il JSONB non
    # vincolava nulla): normalizzale prima di applicare il CHECK, così la
    # migrazione non fallisce su un dato che nessuno può più correggere.
    op.execute(
        f"""
        UPDATE messages
        SET sender_type = CASE WHEN direction = 'in' THEN 'customer' ELSE 'ai' END
        WHERE sender_type NOT IN ({_in_list(_SENDER_TYPES)})
        """
    )
    op.alter_column("messages", "sender_type", nullable=False)
    op.create_check_constraint(
        "ck_messages_sender_type", "messages", f"sender_type IN ({_in_list(_SENDER_TYPES)})"
    )

    op.execute(
        """
        CREATE INDEX ix_messages_automation
        ON messages (merchant_id, automation_id, created_at) WHERE automation_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_messages_profile
        ON messages (merchant_id, profile_id, created_at) WHERE profile_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_messages_reply_to
        ON messages (reply_to_message_id) WHERE reply_to_message_id IS NOT NULL
        """
    )
    # Serve alla risoluzione last-touch ("ultimo outbound di questa
    # conversazione prima di adesso"), che gira su ogni inbound.
    op.create_index("ix_messages_conv_created", "messages", ["conversation_id", "created_at"])

    # ------------------------------------------------------------------ #
    # 6 · analytics_events — le stesse dimensioni sul log (ADR 0021 §V2)
    # ------------------------------------------------------------------ #
    op.add_column(
        "analytics_events", sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "analytics_events", sa.Column("automation_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    # La GROUP BY event_type per finestra è la query più calda della dashboard e
    # oggi ha solo indici a colonna singola.
    op.create_index(
        "ix_analytics_events_merchant_type_occurred",
        "analytics_events",
        ["merchant_id", "event_type", "occurred_at"],
    )

    # ------------------------------------------------------------------ #
    # 7 · RLS
    # ------------------------------------------------------------------ #
    # Le due tabelle-libreria hanno `merchant_id` nullable, quindi il predicato
    # merchant-scoped standard (EXISTS su merchants) le renderebbe invisibili:
    # con merchant_id NULL l'EXISTS è falso. Si usa la forma di
    # `tenant_or_merchant_isolation_users` (0001), con USING e WITH CHECK
    # asimmetrici:
    #   - lettura: un merchant vede i propri profili E quelli di libreria;
    #   - scrittura: un utente merchant può scrivere solo righe intestate a sé
    #     (niente merchant_id NULL), mentre un utente d'agenzia — che non porta
    #     la claim merchant_id — può scrivere anche la libreria.
    jwt = "current_setting('request.jwt.claims', true)::jsonb"
    read_predicate = f"""
        ({jwt} ->> 'tenant_id')::uuid = tenant_id
        AND (
            merchant_id IS NULL
            OR ({jwt} ->> 'merchant_id') IS NULL
            OR ({jwt} ->> 'merchant_id')::uuid = merchant_id
        )
    """
    write_predicate = f"""
        ({jwt} ->> 'tenant_id')::uuid = tenant_id
        AND (
            ({jwt} ->> 'merchant_id') IS NULL
            OR ({jwt} ->> 'merchant_id')::uuid = merchant_id
        )
    """
    for table in _LIBRARY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_or_merchant_isolation_{table} ON {table}
            USING ({read_predicate})
            WITH CHECK ({write_predicate})
            """
        )

    # `lead_outcomes` ha merchant_id NOT NULL → predicato merchant-scoped
    # standard, identico a quello di 0014/0027.
    merchant_predicate = """
        EXISTS (
            SELECT 1 FROM merchants m
            WHERE m.id = lead_outcomes.merchant_id
              AND m.tenant_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'tenant_id')::uuid
              AND (
                  (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id') IS NULL
                  OR m.id = (current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')::uuid
              )
        )
    """
    op.execute("ALTER TABLE lead_outcomes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lead_outcomes FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY merchant_isolation_lead_outcomes ON lead_outcomes
        USING ({merchant_predicate})
        WITH CHECK ({merchant_predicate})
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analytics_events_merchant_type_occurred")
    op.drop_column("analytics_events", "automation_id")
    op.drop_column("analytics_events", "profile_id")

    op.execute("DROP INDEX IF EXISTS ix_messages_conv_created")
    op.execute("DROP INDEX IF EXISTS ix_messages_reply_to")
    op.execute("DROP INDEX IF EXISTS ix_messages_profile")
    op.execute("DROP INDEX IF EXISTS ix_messages_automation")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS ck_messages_sender_type")
    for column in (
        "reply_to_message_id",
        "automation_node_key",
        "automation_id",
        "profile_id",
        "sender_type",
    ):
        op.drop_column("messages", column)

    op.execute("DROP INDEX IF EXISTS ix_conversations_profile")
    op.drop_column("conversations", "profile_id")

    op.execute("DROP TABLE IF EXISTS lead_outcomes CASCADE")
    op.execute("DROP TABLE IF EXISTS outcome_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS conversation_profiles CASCADE")
