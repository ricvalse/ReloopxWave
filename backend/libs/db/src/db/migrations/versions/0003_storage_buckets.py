"""storage buckets + merchant-scoped object RLS

Revision ID: 0003_storage_buckets
Revises: 0002_auth_jwt_hook
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0003_storage_buckets"
down_revision: str | Sequence[str] | None = "0002_auth_jwt_hook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUCKETS = [
    (
        "kb-documents",
        20 * 1024 * 1024,
        [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
        ],
    ),
    (
        "ft-training-data",
        512 * 1024 * 1024,
        ["application/jsonl", "application/json", "application/x-ndjson"],
    ),
    ("analytics-exports", 128 * 1024 * 1024, ["text/csv"]),
    (
        "branding-assets",
        4 * 1024 * 1024,
        ["image/png", "image/jpeg", "image/svg+xml", "image/webp"],
    ),
]

_MERCHANT_PREFIX_PREDICATE = (
    "bucket_id = '{bucket}' AND (storage.foldername(name))[1] = "
    "(current_setting('request.jwt.claims', true)::jsonb ->> 'merchant_id')"
)


def upgrade() -> None:
    for bucket, limit, mimes in _BUCKETS:
        mime_array = "ARRAY[" + ",".join(f"'{m}'" for m in mimes) + "]"
        op.execute(
            f"""
            INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
            VALUES ('{bucket}', '{bucket}', false, {limit}, {mime_array})
            ON CONFLICT (id) DO NOTHING
            """
        )

    # kb-documents: merchant-prefixed path, authenticated CRUD.
    for suffix, stmt in (
        ("read",   "FOR SELECT TO authenticated USING"),
        ("write",  "FOR INSERT TO authenticated WITH CHECK"),
        ("update", "FOR UPDATE TO authenticated USING"),
        ("delete", "FOR DELETE TO authenticated USING"),
    ):
        op.execute(f"DROP POLICY IF EXISTS kb_documents_merchant_{suffix} ON storage.objects")
        op.execute(
            f"CREATE POLICY kb_documents_merchant_{suffix} ON storage.objects "
            f"{stmt} ({_MERCHANT_PREFIX_PREDICATE.format(bucket='kb-documents')})"
        )

    for suffix, stmt in (
        ("read",  "FOR SELECT TO authenticated USING"),
        ("write", "FOR INSERT TO authenticated WITH CHECK"),
    ):
        op.execute(f"DROP POLICY IF EXISTS branding_assets_merchant_{suffix} ON storage.objects")
        op.execute(
            f"CREATE POLICY branding_assets_merchant_{suffix} ON storage.objects "
            f"{stmt} ({_MERCHANT_PREFIX_PREDICATE.format(bucket='branding-assets')})"
        )


def downgrade() -> None:
    for bucket in ("kb-documents", "branding-assets"):
        for suffix in ("read", "write", "update", "delete"):
            op.execute(
                f"DROP POLICY IF EXISTS {bucket.replace('-', '_')}_merchant_{suffix} ON storage.objects"
            )
    for bucket, *_ in _BUCKETS:
        op.execute(f"DELETE FROM storage.buckets WHERE id = '{bucket}'")
