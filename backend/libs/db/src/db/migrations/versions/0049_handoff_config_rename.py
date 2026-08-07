"""Rinomina la sezione di config `escalation` in `handoff` (ADR 0026).

Il rename tocca sei chiavi, di cui due cambiano anche nome di foglia perché
dentro una sezione già chiamata `handoff` il prefisso era ridondante:

    escalation.enabled                    → handoff.enabled
    escalation.handoff_message            → handoff.message
    escalation.silent_handoff             → handoff.silent
    escalation.critical_keywords          → handoff.critical_keywords
    escalation.sla_minutes                → handoff.sla_minutes
    escalation.phone_echo_pause_minutes   → handoff.phone_echo_pause_minutes

**Il codice funziona anche senza questa migrazione**: `LEGACY_KEY_ALIASES` nel
resolver e i `validation_alias` di `HandoffConfig` leggono entrambe le forme.
Questa migrazione serve a chiudere il cerchio, così l'alias diventa rimovibile
invece di restare per sempre — e perché finché il bag contiene la sezione
vecchia il pannello merchant mostrerebbe «Ereditato» su campi in realtà
personalizzati (il frontend decide il badge guardando le chiavi presenti nel bag
raw, non i valori risolti).

Tre superfici hanno un override-bag della stessa shape:
  * `bot_configs.overrides`           — override del merchant
  * `bot_templates.defaults`          — default del template d'agenzia
  * `conversation_profiles.overrides` — profili di conversazione (ADR 0022)

più `bot_templates.locked_keys`, che è un array JSONB di chiavi puntate validato
contro `ConfigKey`.

Le allowlist di azioni (`conversation.playbook.actions.enabled` nei bag e
`allowed_actions` sui nodi automazione) contengono ancora `escalate_human`:
NON vengono toccate qui di proposito — `normalize_action_kind` le traduce in
lettura, e riscriverle significherebbe modificare grafi disegnati dai merchant
per un cambio puramente nominale.

Il downgrade riporta la sezione al nome e alle foglie di prima, così un rollback
del codice ritrova i bag nella forma che si aspetta.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Il revision id finisce in `alembic_version.version_num`, che è VARCHAR(32):
# un id più lungo fa fallire il deploy dell'API *in silenzio* (già successo con
# la 0047). Questo è lungo 26.
revision: str = "0049_handoff_config_rename"
down_revision: str | Sequence[str] | None = "0048_drop_no_answer_config_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BAGS = (
    ("bot_configs", "overrides"),
    ("bot_templates", "defaults"),
    ("conversation_profiles", "overrides"),
)

# foglie rinominate, nella direzione dell'upgrade: vecchio → nuovo.
_RENAMED_LEAVES: tuple[tuple[str, str], ...] = (
    ("handoff_message", "message"),
    ("silent_handoff", "silent"),
)


def _rename_section(
    table: str, column: str, src: str, dst: str, leaves: tuple[tuple[str, str], ...]
) -> None:
    """Sposta la sezione `src` in `dst`, rimappando le foglie rinominate.

    `jsonb_exists` e non l'operatore `?`: quest'ultimo collide con il
    placeholder dei parametri di alcuni driver DBAPI.

    Un eventuale `dst` già presente vince sulle chiavi omonime provenienti da
    `src`: se qualcuno ha già salvato con il nome nuovo, quel valore è il più
    recente e non va sovrascritto da un residuo legacy.
    """
    renames = "".join(
        f"""
            || CASE WHEN jsonb_exists({column}->'{src}', '{old}')
                    THEN jsonb_build_object('{new}', {column}->'{src}'->'{old}')
                    ELSE '{{}}'::jsonb END"""
        for old, new in leaves
    )
    dropped = " ".join(f"- '{old}'" for old, _ in leaves)
    op.execute(
        f"""
        UPDATE {table}
        SET {column} = ({column} - '{src}') || jsonb_build_object(
            '{dst}',
            (({column}->'{src}') {dropped}){renames}
                || coalesce({column}->'{dst}', '{{}}'::jsonb)
        )
        WHERE jsonb_exists({column}, '{src}')
          AND jsonb_typeof({column}->'{src}') = 'object'
        """
    )


def _rename_locked_keys(src_prefix: str, dst_prefix: str, leaf_map: dict[str, str]) -> None:
    """Riscrive il prefisso (e le foglie rinominate) dentro `locked_keys`.

    Senza questo, un lock d'agenzia su `escalation.sla_minutes` non
    corrisponderebbe più a nessuna `ConfigKey`: il salvataggio del template
    verrebbe rifiutato, e — peggio, perché silenzioso — lo strip del lock non
    troverebbe più il path, quindi il lock smetterebbe di essere applicato.
    """
    cases = " ".join(
        f"WHEN k = '{src_prefix}.{old}' THEN '{dst_prefix}.{new}'" for old, new in leaf_map.items()
    )
    op.execute(
        f"""
        UPDATE bot_templates
        SET locked_keys = coalesce(
            (
                SELECT jsonb_agg(
                    CASE {cases}
                         WHEN k LIKE '{src_prefix}.%'
                           THEN '{dst_prefix}.' || substring(k from {len(src_prefix) + 2})
                         ELSE k END
                )
                FROM jsonb_array_elements_text(locked_keys) AS k
            ),
            '[]'::jsonb
        )
        WHERE locked_keys IS NOT NULL
          AND jsonb_typeof(locked_keys) = 'array'
          AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(locked_keys) AS k
            WHERE k LIKE '{src_prefix}.%'
        )
        """
    )


def upgrade() -> None:
    for table, column in _BAGS:
        _rename_section(table, column, "escalation", "handoff", _RENAMED_LEAVES)
    _rename_locked_keys("escalation", "handoff", dict(_RENAMED_LEAVES))


def downgrade() -> None:
    reversed_leaves = tuple((new, old) for old, new in _RENAMED_LEAVES)
    for table, column in _BAGS:
        _rename_section(table, column, "handoff", "escalation", reversed_leaves)
        # `instructions` è nato con l'ADR 0026 e non ha un equivalente legacy:
        # portarselo dentro `escalation` lo renderebbe un campo sconosciuto per
        # il vecchio `EscalationConfig`, che ha `extra="forbid"` — cioè un
        # rollback del codice troverebbe bag che non validano più. Si perde la
        # configurazione dei criteri, che però il codice a cui si torna non
        # saprebbe comunque leggere.
        op.execute(
            f"""
            UPDATE {table}
            SET {column} = jsonb_set(
                {column}, '{{escalation}}', ({column}->'escalation') - 'instructions'
            )
            WHERE jsonb_exists({column}, 'escalation')
              AND jsonb_typeof({column}->'escalation') = 'object'
              AND jsonb_exists({column}->'escalation', 'instructions')
            """
        )
    _rename_locked_keys("handoff", "escalation", dict(reversed_leaves))
    # I lock sui criteri non hanno equivalente legacy: si tolgono, altrimenti
    # `_validate_locked_keys` del codice vecchio li rifiuterebbe.
    op.execute(
        """
        UPDATE bot_templates
        SET locked_keys = coalesce(
            (
                SELECT jsonb_agg(k)
                FROM jsonb_array_elements_text(locked_keys) AS k
                WHERE k NOT LIKE 'escalation.instructions%'
            ),
            '[]'::jsonb
        )
        WHERE locked_keys IS NOT NULL
          AND jsonb_typeof(locked_keys) = 'array'
          AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(locked_keys) AS k
            WHERE k LIKE 'escalation.instructions%'
        )
        """
    )
