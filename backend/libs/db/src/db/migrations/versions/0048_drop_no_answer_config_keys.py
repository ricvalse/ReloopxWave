"""Rimuove le chiavi `no_answer.*` dai bag di configurazione già salvati.

Le chiavi `no_answer.first_reminder_min` / `second_reminder_min` /
`max_followups` / `first_reminder_text` / `second_reminder_text` non sono più
lette da nessuna riga di codice: la cadenza dei follow-up vive interamente sulla
lavagnetta (ADR 0014/0015 — il ritardo sta in `delay_minutes` sul nodo trigger,
il contenuto in `free_text`/template sui nodi `send`). Erano però ancora esposte
nel pannello Configurazione del merchant, quindi ci sono bag salvati che le
contengono.

`BotConfigSchema` ha `extra="forbid"`, e viene applicato **anche in lettura**
(`GET /bot-config/{id}/resolved`) e a ogni salvataggio di un template d'agenzia.
Togliere il campo dal modello senza ripulire i bag significherebbe quindi un 500
sul pannello per chiunque avesse toccato quei campi, e un 422 al primo
salvataggio successivo. Da qui questa migrazione: è ciò che rende la rimozione
davvero senza conseguenze.

Tre superfici contengono un override-bag con la stessa shape:
  * `bot_configs.overrides`      — override del merchant
  * `bot_templates.defaults`     — default del template d'agenzia
  * `conversation_profiles.overrides` — profili di conversazione (ADR 0022)

più `bot_templates.locked_keys`, che è una lista di chiavi puntate e viene
validata contro `ConfigKey`: una `no_answer.*` lì dentro farebbe fallire il
prossimo salvataggio del template.

Irreversibile per scelta: il downgrade non può reinventare valori che nessuno
leggeva. Ripristina solo il campo vuoto, così lo schema torna accettabile se si
fa rollback del codice.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048_drop_no_answer_config_keys"
down_revision: str | Sequence[str] | None = "0047_attribution_profiles_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `- 'no_answer'` su jsonb rimuove la chiave di primo livello se c'è e non fa
    # nulla se non c'è, quindi il WHERE serve solo a non riscrivere ogni riga.
    # `jsonb_exists(...)` e non l'operatore `?`: quest'ultimo collide con il
    # placeholder dei parametri di alcuni driver DBAPI.
    for table, column in (
        ("bot_configs", "overrides"),
        ("bot_templates", "defaults"),
        ("conversation_profiles", "overrides"),
    ):
        op.execute(
            f"""
            UPDATE {table}
            SET {column} = {column} - 'no_answer'
            WHERE jsonb_exists({column}, 'no_answer')
            """
        )

    # `locked_keys` è una colonna JSONB che contiene un array di stringhe (non un
    # array nativo Postgres): si smonta con `jsonb_array_elements_text` e si
    # rimonta con `jsonb_agg`. Il coalesce copre il caso "erano tutte no_answer.*",
    # in cui l'aggregato è NULL e non un array vuoto.
    op.execute(
        """
        UPDATE bot_templates
        SET locked_keys = coalesce(
            (
                SELECT jsonb_agg(k)
                FROM jsonb_array_elements_text(locked_keys) AS k
                WHERE k NOT LIKE 'no_answer.%'
            ),
            '[]'::jsonb
        )
        WHERE locked_keys IS NOT NULL
          AND jsonb_typeof(locked_keys) = 'array'
          AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(locked_keys) AS k
            WHERE k LIKE 'no_answer.%'
        )
        """
    )


def downgrade() -> None:
    # Nessun valore da ripristinare: erano inerti già prima della rimozione.
    pass
