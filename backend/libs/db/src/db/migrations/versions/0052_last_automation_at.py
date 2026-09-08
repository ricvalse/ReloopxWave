"""last_automation_at — il timbro che dice "qui l'azienda ha già scritto"

Revision ID: 0052_last_automation_at
Revises: 0051_automation_hours_queue
Create Date: 2026-09-08

ADR 0031. Serve alla modalità `bot.auto_reply_scope = "solo_automazioni"`: il
bot risponde da solo soltanto nelle conversazioni in cui è già partito un invio
automatico — una campagna, un nodo della lavagnetta, un promemoria appuntamento
— mentre chi scrive a freddo resta all'operatore.

Perché una colonna e non un predicato su `messages`. La domanda "a questa
conversazione abbiamo già mandato qualcosa?" è rispondibile anche con un EXISTS
su `messages.automation_id`, che esiste dalla 0047. Ma il gate della risposta
automatica ha la conversazione **già caricata** in sessione: leggere un suo
campo costa zero query, mentre l'EXISTS ne costerebbe una su ogni messaggio in
ingresso di ogni merchant in modalità ristretta. La denormalizzazione qui è
gratis perché il punto di scrittura è già unico (`send_and_persist_decision`).

Perché un timestamp e non un booleano. Risponde alla stessa domanda
(`IS NOT NULL`) ma dice anche *quando*, che è ciò che serve il giorno in cui si
vorrà far decadere il permesso ("solo se l'automazione è delle ultime N ore").
Con un booleano quel giorno servirebbe una seconda migrazione.

Il backfill non è un dettaglio: senza, un merchant che accende la modalità
vedrebbe il bot ammutolire su **tutte** le campagne già in volo, perché il
timbro esisterebbe solo per gli invii successivi al deploy. Con il backfill la
storia è già corretta e le campagne in corso continuano a funzionare.

La clausola su `sender_type` serve **solo qui**, per lo storico: fino a questa
migrazione `appointment_reminder` inviava senza `automation_id` (è uno
scheduler, non ha un flusso dietro), quindi i promemoria già mandati non
risulterebbero. Da qui in avanti il timbro è incondizionato nel punto d'invio,
quindi l'allowlist non serve più a runtime.

RLS: nessuna policy nuova. È una colonna su `conversations`, che è già coperta.
Indice: nessuno. Si legge sempre insieme alla riga, per chiave primaria.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_last_automation_at"
down_revision: str | Sequence[str] | None = "0051_automation_hours_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "conversations"
_COLUMN = "last_automation_at"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill dallo storico scritto dalla 0047. Un solo passaggio aggregato:
    # l'indice `ix_messages_conv_created` serve il GROUP BY, e la tabella
    # `conversations` si aggiorna una riga per conversazione, non una per
    # messaggio.
    # I sender_type sono costanti di codice, non input: stanno in chiaro nell'SQL
    # invece che come parametro. `automation` / `automation_ai` li timbra il
    # motore; `appointment_reminder` è lo scheduler dei promemoria, che fino a
    # questa migrazione non passava `automation_id`.
    op.execute(
        """
        UPDATE conversations AS c
        SET last_automation_at = m.ts
        FROM (
            SELECT conversation_id, MAX(created_at) AS ts
            FROM messages
            WHERE direction = 'out'
              AND (
                  automation_id IS NOT NULL
                  OR sender_type IN ('automation', 'automation_ai', 'appointment_reminder')
              )
            GROUP BY conversation_id
        ) AS m
        WHERE m.conversation_id = c.id
        """
    )


def downgrade() -> None:
    # Il dato è interamente ricostruibile dal backfill, quindi il downgrade non
    # perde nulla che non si possa rifare.
    op.drop_column(_TABLE, _COLUMN)
