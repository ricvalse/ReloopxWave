"""Fine-tuning pipeline — quality filter, eval scoring, FT routing, anonymizer hook."""

from __future__ import annotations

import json
from uuid import uuid4

from workers.fine_tuning.collect import TrainingPair, collect_training_pairs
from workers.fine_tuning.evaluate import extract_prompts, reply_quality
from workers.fine_tuning.quality import filter_pairs

from ai_core.ft import anonymize_text
from ai_core.ft_routing import should_use_ft

# ---- quality filter (2.3) ----


def _pair(conv, user="ciao vorrei info", assistant="certo, ecco le info utili") -> TrainingPair:
    return TrainingPair(conversation_id=conv, user=user, assistant=assistant)


def test_quality_keeps_clean_conversation() -> None:
    c = uuid4()
    report = filter_pairs([_pair(c), _pair(c)])
    assert len(report.kept) == 2
    assert report.dropped == 0


def test_quality_drops_bot_error_conversation() -> None:
    c = uuid4()
    report = filter_pairs([_pair(c), _pair(c, assistant="Si è verificato un errore tecnico")])
    assert report.kept == []
    assert report.reasons.get("bot_error") == 2


def test_quality_drops_premature_dropoff() -> None:
    report = filter_pairs([_pair(uuid4())])  # single-turn conversation
    assert report.kept == []
    assert report.reasons.get("premature_dropoff") == 1


def test_quality_drops_empty_turn() -> None:
    c = uuid4()
    report = filter_pairs([_pair(c), _pair(c, assistant="ok")])  # 'ok' < min chars
    assert len(report.kept) == 1
    assert report.reasons.get("empty_turn") == 1


# ---- evaluator (2.4) ----


def test_reply_quality_valid() -> None:
    assert reply_quality(json.dumps({"reply_text": "ciao", "actions": []})) == 1.0


def test_reply_quality_empty_reply() -> None:
    assert reply_quality(json.dumps({"reply_text": "  "})) == 0.0


def test_reply_quality_not_json() -> None:
    assert reply_quality("not json at all") == 0.0


def test_extract_prompts_pulls_user_turns() -> None:
    raw = "\n".join(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": f"q{i}"},
                    {"role": "assistant", "content": "a"},
                ]
            }
        )
        for i in range(3)
    ).encode("utf-8")
    assert extract_prompts(raw, limit=2) == ["q0", "q1"]


# ---- FT routing decision (2.5) ----


def test_ft_routing_no_deployed_model() -> None:
    assert (
        should_use_ft(has_deployed_ft=False, ft_experiment_running=True, variant_id="ft") is False
    )


def test_ft_routing_experiment_gates_to_ft_arm() -> None:
    assert should_use_ft(has_deployed_ft=True, ft_experiment_running=True, variant_id="ft") is True
    assert (
        should_use_ft(has_deployed_ft=True, ft_experiment_running=True, variant_id="baseline")
        is False
    )
    assert should_use_ft(has_deployed_ft=True, ft_experiment_running=True, variant_id=None) is False


def test_ft_routing_no_experiment_uses_ft_for_all() -> None:
    assert should_use_ft(has_deployed_ft=True, ft_experiment_running=False, variant_id=None) is True


# ---- collect tenant scoping (2.x — RLS backstop + app filter) ----


class _RecordingResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):  # pragma: no cover - not used here
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Captures the compiled SQL of every statement so we can assert the
    tenant filter is applied; returns no conversations so message fetch is
    skipped."""

    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        return _RecordingResult([])


async def test_collect_filters_by_target_tenant() -> None:
    from datetime import UTC, datetime, timedelta

    target = uuid4()
    other = uuid4()
    session = _RecordingSession()
    until = datetime.now(tz=UTC)
    since = until - timedelta(days=28)

    pairs = await collect_training_pairs(session, tenant_id=target, since=since, until=until)

    # No conversations returned → empty dataset, and only one statement issued.
    assert pairs == []
    assert len(session.statements) == 1
    sql = session.statements[0].lower()
    # The application-level tenant filter is present and scopes to the target,
    # never to the other tenant. (SQLAlchemy renders UUID literals as bare hex.)
    assert "merchants.tenant_id" in sql
    assert target.hex in sql
    assert other.hex not in sql


# ---- anonymizer presidio hook (2.2) ----


def test_anonymize_runs_additional_transform_after_regex() -> None:
    # The presidio layer plugs in via additional_transforms; simulate it with a
    # fake NER redactor and confirm it runs on top of the regex output.
    def fake_ner(text: str) -> str:
        return text.replace("Mario Rossi", "<NAME>")

    report = anonymize_text("Mario Rossi, scrivimi a mario@x.it", additional_transforms=[fake_ner])
    assert "<NAME>" in report.text
    assert "<EMAIL_1>" in report.text
    assert "mario@x.it" not in report.text
