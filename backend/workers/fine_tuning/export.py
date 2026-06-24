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

from ai_core.ft import anonymize_text, build_presidio_transform
from integrations import SupabaseStorage
from shared import Settings, get_logger
from workers.fine_tuning.collect import TrainingPair

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ExportResult:
    tenant_id: UUID
    run_id: str
    train_path: str  # bucket-relative path (train split)
    eval_path: str | None  # bucket-relative path (held-out split), None if too few pairs
    train_count: int
    eval_count: int
    redaction_totals: dict[str, int]


# Quota di coppie tenute fuori dal training per la valutazione held-out (#17).
EVAL_FRACTION = 0.15
# Sotto questa soglia non ha senso uno split: teniamo tutto in train (l'eval
# fallback nel valutatore degrada al training set come smoke-test).
MIN_PAIRS_FOR_SPLIT = 8


async def export_training_pairs(
    *,
    settings: Settings,
    tenant_id: UUID,
    pairs: list[TrainingPair],
) -> ExportResult:
    """Anonymize + split into `train.jsonl` (+ `eval.jsonl`) under
    `<ft_bucket>/<tenant_id>/<run_id>/`.

    The split is stratificato per conversazione: tutte le coppie di una stessa
    conversazione finiscono nello stesso lato (train o eval) così il valutatore
    è davvero held-out e non vede turni di conversazioni già viste in training.

    JSONL shape is OpenAI fine-tuning format:
        {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}
    """
    run_id = uuid4().hex
    train_path = f"{tenant_id}/{run_id}/train.jsonl"
    eval_path = f"{tenant_id}/{run_id}/eval.jsonl"

    # Art. 5.2 double layer: regex (in anonymize_text) + presidio NER (here).
    # Presidio degrades to None outside production (no spaCy model) — we log so
    # the audit trail records whether the NER layer actually ran.
    # In production the NER layer is mandatory (require=True → raises if the
    # spaCy model is missing, so we never ship un-NER'd PII). Elsewhere it
    # degrades to regex-only with a logged warning.
    presidio = build_presidio_transform(language="it", require=settings.environment == "production")
    transforms = [presidio] if presidio is not None else None
    if presidio is None:
        logger.warning("ft.export.presidio_skipped", tenant_id=str(tenant_id))

    totals: dict[str, int] = {}

    def _serialize(pair: TrainingPair) -> str:
        user_report = anonymize_text(pair.user, additional_transforms=transforms)
        assistant_report = anonymize_text(pair.assistant, additional_transforms=transforms)
        _merge_counts(totals, user_report.counts)
        _merge_counts(totals, assistant_report.counts)
        record = {
            "messages": [
                {"role": "user", "content": user_report.text},
                {"role": "assistant", "content": assistant_report.text},
            ]
        }
        return json.dumps(record, ensure_ascii=False)

    train_pairs, eval_pairs = _stratified_split(pairs)
    train_lines = [_serialize(p) for p in train_pairs]
    eval_lines = [_serialize(p) for p in eval_pairs]

    storage = SupabaseStorage(
        project_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        bucket=settings.supabase_ft_bucket,
    )
    await storage.upload_bytes(
        train_path, "\n".join(train_lines).encode("utf-8"), content_type="application/x-ndjson"
    )
    eval_uploaded: str | None = None
    if eval_lines:
        await storage.upload_bytes(
            eval_path, "\n".join(eval_lines).encode("utf-8"), content_type="application/x-ndjson"
        )
        eval_uploaded = eval_path

    logger.info(
        "ft.export.uploaded",
        tenant_id=str(tenant_id),
        run_id=run_id,
        train_pairs=len(train_pairs),
        eval_pairs=len(eval_pairs),
        redactions=totals,
        ner_applied=presidio is not None,
        uploaded_at=datetime.now(tz=UTC).isoformat(),
    )
    return ExportResult(
        tenant_id=tenant_id,
        run_id=run_id,
        train_path=train_path,
        eval_path=eval_uploaded,
        train_count=len(train_pairs),
        eval_count=len(eval_pairs),
        redaction_totals=totals,
    )


def _stratified_split(
    pairs: list[TrainingPair],
) -> tuple[list[TrainingPair], list[TrainingPair]]:
    """Split per-conversazione: ~`EVAL_FRACTION` delle conversazioni va in eval,
    le sue coppie escono interamente dal training. Deterministico (ordinato per
    conversation_id) così una run è riproducibile."""
    if len(pairs) < MIN_PAIRS_FOR_SPLIT:
        return list(pairs), []

    # Raggruppa per conversazione preservando l'ordine d'arrivo delle coppie.
    groups: dict[str, list[TrainingPair]] = {}
    for pair in pairs:
        groups.setdefault(str(pair.conversation_id), []).append(pair)

    conv_ids = sorted(groups)
    n_eval_convs = max(1, round(len(conv_ids) * EVAL_FRACTION))
    # Prendi le ultime N conversazioni (ordine stabile) come held-out.
    eval_ids = set(conv_ids[-n_eval_convs:])

    train: list[TrainingPair] = []
    eval_: list[TrainingPair] = []
    for cid in conv_ids:
        target = eval_ if cid in eval_ids else train
        target.extend(groups[cid])

    # Non svuotare mai il training: se lo split ha tolto troppo, annulla l'eval.
    if not train:
        return list(pairs), []
    return train, eval_


def _merge_counts(into: dict[str, int], add: dict[str, int]) -> None:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v
