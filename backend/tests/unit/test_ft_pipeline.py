"""Fine-tuning pipeline — quality filter, eval scoring, FT routing, anonymizer hook."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from workers.fine_tuning.collect import TrainingPair, collect_training_pairs
from workers.fine_tuning.evaluate import (
    EvalSample,
    extract_prompts,
    extract_samples,
    has_booking_action,
    reply_quality,
)
from workers.fine_tuning.export import _stratified_split
from workers.fine_tuning.quality import filter_pairs

from ai_core.ft import anonymize_text
from ai_core.ft_routing import should_use_ft

# ---- quality filter (2.3) ----


def _pair(conv, user="ciao vorrei info", assistant="certo, ecco le info utili") -> TrainingPair:
    return TrainingPair(conversation_id=conv, user=user, assistant=assistant)


def test_quality_keeps_clean_conversation() -> None:
    c = uuid4()
    # Use distinct user messages so S-07 dedup doesn't collapse identical pairs.
    report = filter_pairs([
        _pair(c, user="ciao vorrei info", assistant="certo, ecco le info utili"),
        _pair(c, user="e il prezzo?", assistant="il prezzo è molto competitivo"),
    ])
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


# ---- held-out split (#17) ----


def test_stratified_split_keeps_conversation_intact() -> None:
    # 10 conversazioni da 2 coppie ciascuna -> split per-conversazione, mai a metà.
    pairs: list[TrainingPair] = []
    convs = [uuid4() for _ in range(10)]
    for c in convs:
        pairs.append(_pair(c))
        pairs.append(_pair(c))

    train, eval_ = _stratified_split(pairs)

    assert len(train) + len(eval_) == len(pairs)
    assert eval_, "deve esserci un held-out set con abbastanza coppie"
    train_convs = {str(p.conversation_id) for p in train}
    eval_convs = {str(p.conversation_id) for p in eval_}
    # Nessuna conversazione compare in entrambi i lati (held-out reale).
    assert train_convs.isdisjoint(eval_convs)


def test_stratified_split_too_few_pairs_no_eval() -> None:
    train, eval_ = _stratified_split([_pair(uuid4()), _pair(uuid4())])
    assert eval_ == []
    assert len(train) == 2


# ---- booking-action quality metric (#17) ----


def test_has_booking_action_true() -> None:
    payload = json.dumps({"reply_text": "ti propongo", "actions": [{"kind": "propose_slots"}]})
    assert has_booking_action(payload) is True


def test_has_booking_action_false_without_booking_kind() -> None:
    payload = json.dumps({"reply_text": "ciao", "actions": [{"kind": "update_score"}]})
    assert has_booking_action(payload) is False
    assert has_booking_action("not json") is False


def test_extract_samples_flags_expected_booking() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "vorrei prenotare"},
                        {"role": "assistant", "content": "certo, che disponibilità preferisci?"},
                    ]
                }
            ),
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "quanto costa?"},
                        {"role": "assistant", "content": "il prezzo è 50 euro"},
                    ]
                }
            ),
        ]
    ).encode("utf-8")
    samples = extract_samples(raw)
    assert samples[0] == EvalSample(prompt="vorrei prenotare", expects_booking=True)
    assert samples[1].expects_booking is False
    # extract_prompts resta un wrapper coerente.
    assert extract_prompts(raw) == ["vorrei prenotare", "quanto costa?"]


# ---- deploy apre un esperimento A/B quando l'FT è legato a un merchant (#16) ----


async def test_deploy_opens_running_ab_experiment_with_ft_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers.fine_tuning import handlers as h

    tenant_id = uuid4()
    merchant_id = uuid4()
    ft_row_id = uuid4()

    ft_row = SimpleNamespace(
        id=ft_row_id,
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        version=3,
        provider_model_id="ft:model:abc",
        status="evaluated",
        is_default=False,
    )

    class FakeSession:
        async def get(self, _model, _id):
            return ft_row

        async def execute(self, _stmt):  # clears is_default on siblings — no-op here
            return None

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    created: dict = {}
    statuses: list[tuple] = []

    class FakeAB:
        def __init__(self, _session):
            pass

        async def has_running(self, _merchant_id, *, exclude_id=None):
            return False

        async def create(self, **kw):
            created.update(kw)
            return SimpleNamespace(id=uuid4())

        async def set_status(self, exp_id, *, status, started_at=None):
            statuses.append((exp_id, status, started_at))

    monkeypatch.setattr(h, "session_scope", fake_scope)
    # deploy fa `from db import ABRepository` a runtime → patcha lì.
    import db

    monkeypatch.setattr(db, "ABRepository", FakeAB)

    result = await h.fine_tune_deploy({}, str(ft_row_id))

    # Esperimento creato con arm baseline + ft, portato a running.
    arm_ids = {v["id"] for v in created["variants"]}
    assert arm_ids == {"baseline", "ft"}
    assert created["merchant_id"] == merchant_id
    assert statuses and statuses[0][1] == "running"
    assert result["ab_experiment_id"] is not None
    # L'FT row è promosso a default + deployed.
    assert ft_row.is_default is True
    assert ft_row.status == "deployed"


async def test_deploy_rejects_eval_skipped_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.fine_tuning import handlers as h

    from shared import IntegrationError

    ft_row = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=None,
        status="eval_skipped",
    )

    class FakeSession:
        async def get(self, _model, _id):
            return ft_row

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(h, "session_scope", fake_scope)

    with pytest.raises(IntegrationError):
        await h.fine_tune_deploy({}, str(ft_row.id))


# ---- deploy NON apre un secondo esperimento se ce n'è già uno running (#guard) ----


async def test_deploy_skips_ab_when_experiment_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Se il merchant ha già un esperimento running, aprirne un secondo dirotterebbe
    # il traffico (`_assign_ab_variant` onora solo il più vecchio). Il deploy deve
    # rispettare la guardia has_running: niente create/set_status, FT comunque
    # deployato via is_default.
    from workers.fine_tuning import handlers as h

    tenant_id = uuid4()
    merchant_id = uuid4()
    ft_row_id = uuid4()

    ft_row = SimpleNamespace(
        id=ft_row_id,
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        version=4,
        provider_model_id="ft:model:def",
        status="evaluated",
        is_default=False,
    )

    class FakeSession:
        async def get(self, _model, _id):
            return ft_row

        async def execute(self, _stmt):
            return None

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    created: list = []
    statuses: list = []

    class FakeAB:
        def __init__(self, _session):
            pass

        async def has_running(self, _merchant_id, *, exclude_id=None):
            return True

        async def create(self, **kw):
            created.append(kw)
            return SimpleNamespace(id=uuid4())

        async def set_status(self, exp_id, *, status, started_at=None):
            statuses.append((exp_id, status, started_at))

    monkeypatch.setattr(h, "session_scope", fake_scope)
    import db

    monkeypatch.setattr(db, "ABRepository", FakeAB)

    result = await h.fine_tune_deploy({}, str(ft_row_id))

    # Nessun esperimento aperto, nessuno stato cambiato.
    assert created == []
    assert statuses == []
    assert result["ab_experiment_id"] is None
    # L'FT è comunque deployato (default tenant-wide via is_default).
    assert ft_row.is_default is True
    assert ft_row.status == "deployed"


# ---- /fine-tuning/run valida che il target merchant sia del tenant del caller ----


async def test_run_fine_tune_rejects_cross_tenant_merchant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routers import fine_tuning as ft_router
    from shared import NotFoundError

    caller_tenant = uuid4()
    other_tenant = uuid4()
    target_merchant_id = uuid4()

    class FakeMerchantRepo:
        def __init__(self, _session):
            pass

        async def get(self, _merchant_id):
            # Il merchant esiste ma appartiene a un altro tenant.
            return SimpleNamespace(id=target_merchant_id, tenant_id=other_tenant)

    monkeypatch.setattr(ft_router, "MerchantRepository", FakeMerchantRepo)

    class FakeArq:
        def __init__(self):
            self.calls: list = []

        async def enqueue_job(self, *a, **kw):
            self.calls.append((a, kw))

    arq = FakeArq()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(arq=arq)))
    ctx = SimpleNamespace(tenant_id=caller_tenant)
    payload = ft_router.RunFineTuneIn(target_merchant_id=target_merchant_id)

    with pytest.raises(NotFoundError):
        await ft_router.run_fine_tune(payload, ctx, object(), request)  # type: ignore[arg-type]

    # Nessun job accodato quando la validazione fallisce.
    assert arq.calls == []


async def test_run_fine_tune_enqueues_for_owned_merchant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routers import fine_tuning as ft_router

    caller_tenant = uuid4()
    target_merchant_id = uuid4()

    class FakeMerchantRepo:
        def __init__(self, _session):
            pass

        async def get(self, _merchant_id):
            return SimpleNamespace(id=target_merchant_id, tenant_id=caller_tenant)

    monkeypatch.setattr(ft_router, "MerchantRepository", FakeMerchantRepo)

    class FakeArq:
        def __init__(self):
            self.calls: list = []

        async def enqueue_job(self, *a, **kw):
            self.calls.append((a, kw))

    arq = FakeArq()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(arq=arq)))
    ctx = SimpleNamespace(tenant_id=caller_tenant)
    payload = ft_router.RunFineTuneIn(target_merchant_id=target_merchant_id)

    result = await ft_router.run_fine_tune(payload, ctx, object(), request)  # type: ignore[arg-type]

    assert result["enqueued"] is True
    assert len(arq.calls) == 1
    _args, kwargs = arq.calls[0]
    assert kwargs["target_merchant_id"] == str(target_merchant_id)
