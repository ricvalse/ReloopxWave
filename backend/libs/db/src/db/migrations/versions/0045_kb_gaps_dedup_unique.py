"""kb_gaps: collassa i duplicati e aggiunge l'unique (merchant_id, md5(question_text))

Bug: `RAGEngine._log_gap` faceva `INSERT ... ON CONFLICT DO NOTHING`, ma `kb_gaps`
non aveva alcun vincolo unique su cui il conflitto potesse scattare → ogni turno
inseriva una riga nuova e `frequency` restava sempre 1 (mai incrementata),
contrariamente all'intento del codice.

Questa migrazione:
1. collassa le righe duplicate esistenti in una sola per (merchant_id, question_text),
   sommando `frequency` e tenendo il `last_seen_at` più recente;
2. crea l'unique index `uq_kb_gaps_merchant_question` sull'espressione
   `(merchant_id, md5(question_text))` — md5 evita il limite di dimensione del btree
   su `question_text` (troncato a 1000 char, potenzialmente multibyte).

Il retriever ora usa `ON CONFLICT (merchant_id, md5(question_text)) DO UPDATE SET
frequency = frequency + 1, last_seen_at = now()`.

RLS: `kb_gaps` è FORCE ROW LEVEL SECURITY e la migrazione gira senza claim JWT →
UPDATE/DELETE vedrebbero ZERO righe (policy fail-closed). Togliamo temporaneamente
FORCE per il passo dati (l'owner bypassa la RLS quando non è forzata), poi ripristiniamo
— stesso pattern di 0028 / 0043. La creazione dell'index è DDL e non è gated dalla RLS.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0045_kb_gaps_dedup_unique"
down_revision: str | Sequence[str] | None = "0044_drop_automation_system_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Superstite = riga più recente del gruppo (rn = 1). Postgres non ha min(uuid),
# quindi si usa row_number() invece di un aggregato sull'id. La frequency della
# superstite diventa la somma del gruppo; le altre righe vengono eliminate.
# UPDATE e DELETE usano lo STESSO ordinamento → stessa superstite.
_COLLAPSE = """
WITH ranked AS (
    SELECT id,
           sum(frequency) OVER (PARTITION BY merchant_id, md5(question_text)) AS total_freq,
           max(last_seen_at) OVER (PARTITION BY merchant_id, md5(question_text)) AS latest,
           row_number() OVER (
               PARTITION BY merchant_id, md5(question_text)
               ORDER BY last_seen_at DESC, id
           ) AS rn
    FROM kb_gaps
)
UPDATE kb_gaps k
SET frequency = r.total_freq,
    last_seen_at = r.latest
FROM ranked r
WHERE k.id = r.id AND r.rn = 1
  AND (k.frequency <> r.total_freq OR k.last_seen_at <> r.latest);
"""

_DELETE_DUPES = """
DELETE FROM kb_gaps k
USING (
    SELECT id,
           row_number() OVER (
               PARTITION BY merchant_id, md5(question_text)
               ORDER BY last_seen_at DESC, id
           ) AS rn
    FROM kb_gaps
) d
WHERE k.id = d.id AND d.rn > 1;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE kb_gaps NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute(_COLLAPSE)
        op.execute(_DELETE_DUPES)
    finally:
        op.execute("ALTER TABLE kb_gaps FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_gaps_merchant_question "
        "ON kb_gaps (merchant_id, md5(question_text))"
    )


def downgrade() -> None:
    # Le righe collassate non sono ripristinabili (nessuna provenienza salvata);
    # si toglie solo l'unique index.
    op.execute("DROP INDEX IF EXISTS uq_kb_gaps_merchant_question")
