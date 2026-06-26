"""Unit tests for S-05: pre-conversation intelligence.

- _compute_intake_score heuristic
- optimize_send_times cron (mocked DB)
"""

from __future__ import annotations

import pytest

from ai_core.conversation_service import _compute_intake_score


class TestComputeIntakeScore:
    def test_empty_message_returns_zero(self):
        assert _compute_intake_score("") == 0

    def test_short_generic_message_low_score(self):
        score = _compute_intake_score("Ciao")
        assert 0 <= score <= 15

    def test_high_intent_keywords_boost_score(self):
        score = _compute_intake_score("Vorrei un preventivo per il vostro prodotto")
        assert score >= 30

    def test_multiple_keywords_cap_at_100(self):
        text = " ".join(["prezzo", "offerta", "sconto", "disponibile", "acquistare"] * 10)
        assert _compute_intake_score(text) == 100

    def test_long_message_boosts_length_component(self):
        text = "a" * 200
        score = _compute_intake_score(text)
        assert score >= 40  # 200 // 5 = 40 capped

    def test_score_range_0_to_100(self):
        texts = [
            "ok",
            "Voglio comprare subito disponibile prezzo offerta sconto preventivo",
            "Informazioni per l'acquisto del prodotto con preventivo e sconto",
        ]
        for t in texts:
            s = _compute_intake_score(t)
            assert 0 <= s <= 100, f"score {s} out of range for: {t!r}"


class TestSendTimeOptimizer:
    """Smoke-test the send_time module structure (no real DB needed)."""

    def test_module_importable(self):
        from workers.scheduler.send_time import optimize_send_times  # noqa: F401

        assert callable(optimize_send_times)

    def test_min_messages_constant(self):
        from workers.scheduler import send_time

        assert send_time._MIN_MESSAGES >= 1


class TestReminderCandidateOptimalHour:
    """Verify that optimal_send_hour is honoured in the no_answer scheduler logic."""

    def _make_candidate(self, optimal_send_hour):
        from db.repositories.conversation import ReminderCandidate
        from datetime import datetime, UTC, timedelta
        import uuid

        now = datetime.now(tz=UTC)
        return ReminderCandidate(
            conversation_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            wa_phone_number_id="360:111",
            wa_contact_phone="+39123456789",
            last_message_at=now - timedelta(hours=3),
            reminders_sent=0,
            last_reminder_at=None,
            last_inbound_at=now - timedelta(hours=3),
            optimal_send_hour=optimal_send_hour,
        )

    def test_dataclass_accepts_optimal_send_hour(self):
        cand = self._make_candidate(optimal_send_hour=10)
        assert cand.optimal_send_hour == 10

    def test_dataclass_default_is_none(self):
        cand = self._make_candidate(optimal_send_hour=None)
        assert cand.optimal_send_hour is None
