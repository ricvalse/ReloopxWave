"""whatsapp-media storage bucket + merchant-scoped object RLS

Private bucket for inbound WhatsApp media (image/audio/video/document/sticker).
Objects are keyed `{merchant_id}/{conversation_id}/{message_id}{ext}`; the SELECT
policy scopes reads to the caller's own `merchant_id` prefix (same convention as
`branding-assets` / `kb-documents`, migration 0003). Writes are service-role
from the worker (which has no user JWT) — the app-code prefix guard on the
signing endpoint is what enforces isolation there.

Revision ID: 0046_whatsapp_media_bucket
Revises: 0045_kb_gaps_dedup_unique
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046_whatsapp_media_bucket"
down_revision: str | Sequence[str] | None = "0045_kb_gaps_dedup_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUCKET = "whatsapp-media"
_SIZE_LIMIT = 20 * 1024 * 1024
_MIMES = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/3gpp",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/amr",
    "application/pdf",
]

# Same predicate shape as migration 0003: first path segment must equal the
# caller's merchant_id claim. Reads go direct-to-Supabase from the merchant
# portal under RLS; the worker's service-role writes bypass this (guarded in app
# code on the signed-URL endpoint).
_MERCHANT_PREFIX_PREDICATE = (
    "bucket_id = '{bucket}' AND (storage.foldername(name))[1] = "
    "(current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')"
)


def upgrade() -> None:
    mime_array = "ARRAY[" + ",".join(f"'{m}'" for m in _MIMES) + "]"
    op.execute(
        f"""
        INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
        VALUES ('{_BUCKET}', '{_BUCKET}', false, {_SIZE_LIMIT}, {mime_array})
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute("DROP POLICY IF EXISTS whatsapp_media_merchant_read ON storage.objects")
    op.execute(
        "CREATE POLICY whatsapp_media_merchant_read ON storage.objects "
        "FOR SELECT TO authenticated USING "
        f"({_MERCHANT_PREFIX_PREDICATE.format(bucket=_BUCKET)})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS whatsapp_media_merchant_read ON storage.objects")
    op.execute(f"DELETE FROM storage.buckets WHERE id = '{_BUCKET}'")
