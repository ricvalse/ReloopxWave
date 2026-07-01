"""WhatsApp template header image: canonical storage path

Revision ID: 0041_tpl_header_image_path
Revises: 0040_appt_status_backfill
Create Date: 2026-07-01

Aggiunge la colonna `header_image_path` a `whatsapp_templates`: il path
merchant-prefissato dell'immagine d'intestazione su Supabase Storage (bucket
`branding-assets`). È l'artefatto durevole da cui si genera lo `header_image_url`
(signed URL a lunga scadenza) usato sia come `header_handle` in fase di submit a
360dialog/Meta, sia come `image.link` in fase di invio.

Nessuna nuova policy RLS: la riga vive su `whatsapp_templates` (già RLS
merchant-scoped) e il bucket `branding-assets` ha già le policy create in 0003.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_tpl_header_image_path"
down_revision = "0040_appt_status_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_templates",
        sa.Column("header_image_path", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_templates", "header_image_path")
