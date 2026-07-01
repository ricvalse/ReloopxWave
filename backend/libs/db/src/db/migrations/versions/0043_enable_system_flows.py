"""enable system lifecycle flows by default (ADR 0011, approccio A2/B2)

Le automazioni di sistema (no_answer / reactivation / booking_reminder) sono ora
configurate SOLO dalla lavagnetta (le 3 card numeriche sono state rimosse) e sono
**attive di default** — il loro grafo di default rispecchia i default di config, quindi
è lo stesso comportamento storico "nessun flusso → invia coi default".

Il seeding lazy (`ensure_system_automations`) ora crea i flussi con `enabled=true`.
Questa migrazione sistema i merchant che avevano già aperto la pagina Automazioni PRIMA
del cambio: avevano un flusso di sistema seminato con il vecchio grafo minimo (trigger +
un solo nodo `send` di default) e **disabilitato** → quindi con i promemoria SPENTI.

Strategia mirata e sicura: ELIMINA solo i flussi di sistema ancora al default intoccato
(esattamente 2 nodi: trigger + 1 send con free_text/template NULL, disabilitati). Vengono
riseminati (grafo ricco + attivo) alla prossima apertura pagina; nel frattempo gli scheduler
li trattano come "nessun flusso" → inviano coi default (attivi). I flussi PERSONALIZZATI dal
merchant (nodi aggiunti o testo/template impostati) NON vengono toccati: restano com'erano.
`first_contact` è escluso (inerte, nascosto in UI).

RLS: `automation_*` sono FORCE ROW LEVEL SECURITY, e la migrazione gira senza claim JWT →
una DELETE vedrebbe ZERO righe (policy fail-closed). Togliamo temporaneamente FORCE (l'owner
bypassa la RLS quando non è forzata), eseguiamo, poi ripristiniamo — come in 0028.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0043_enable_system_flows"
down_revision: str | Sequence[str] | None = "0042_drop_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = ("automation_flows", "automation_nodes", "automation_edges")

# Delete only the untouched default seed (trigger + single default `send`, disabled).
# The FK cascade clears the flow's nodes/edges.
_DELETE_UNTOUCHED = """
DELETE FROM automation_flows f
WHERE f.system_key IS NOT NULL
  AND f.system_key <> 'first_contact'
  AND f.enabled = false
  AND (SELECT count(*) FROM automation_nodes n WHERE n.automation_id = f.id) = 2
  AND EXISTS (
        SELECT 1 FROM automation_nodes n
        WHERE n.automation_id = f.id
          AND n.type = 'send'
          AND (n.config ->> 'free_text') IS NULL
          AND (n.config ->> 'template_id') IS NULL
  );
"""


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(_DELETE_UNTOUCHED)
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Irreversible: the deleted flows are re-seeded (disabled would need per-flow
    # provenance we don't store). No-op; `ensure_system_automations` recreates them.
    pass
