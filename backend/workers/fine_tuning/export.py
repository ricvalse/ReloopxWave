"""Anonymize + serialize a list of TrainingPairs to JSONL, upload to Supabase
Storage, return the path a subsequent `fine_tune_train` job can consume.

This is the contractual Art. 5.2 boundary: nothing crosses the anonymizer
without a report. The report is returned to the caller so the worker can log
`(tag, count)` pairs into the FT audit trail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_core.ft import anonymize_text
from integrations import SupabaseStorage
from shared import Settings, get_logger
from workers.fine_tuning.collect import TrainingPair

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ExportResult:
    tenant_id: UUID
    run_id: str
    path: str  # bucket-relative path
    pairs_count: int
    redaction_totals: dict[str, int]


async def export_training_pairs(
    *,
    settings: Settings,
    tenant_id: UUID,
    pairs: list[TrainingPair],
) -> ExportResult:
    """Write `train.jsonl` to `<ft_bucket>/<tenant_id>/<run_id>/train.jsonl`.

    JSONL shape is OpenAI fine-tuning format:
        {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}
    """
    run_id = uuid4().hex
    path = f"{tenant_id}/{run_id}/train.jsonl"

    lines: list[str] = []
    totals: dict[str, int] = {}
    for pair in pairs:
        user_report = anonymize_text(pair.user)
        assistant_report = anonymize_text(pair.assistant)
        _merge_counts(totals, user_report.counts)
        _merge_counts(totals, assistant_report.counts)

        record = {
            "messages": [
                {"role": "user", "content": user_report.text},
                {"role": "assistant", "content": assistant_report.text},
            ]
        }
        lines.append(json.dumps(record, ensure_ascii=False))

    body = "\n".join(lines).encode("utf-8")

    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_ft_bucket,
    )
    await storage.upload_bytes(path, body, content_type="application/x-ndjson")

    logger.info(
        "ft.export.uploaded",
        tenant_id=str(tenant_id),
        run_id=run_id,
        pairs=len(pairs),
        redactions=totals,
        uploaded_at=datetime.now(tz=UTC).isoformat(),
    )
    return ExportResult(
        tenant_id=tenant_id,
        run_id=run_id,
        path=path,
        pairs_count=len(pairs),
        redaction_totals=totals,
    )


def _merge_counts(into: dict[str, int], add: dict[str, int]) -> None:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v
