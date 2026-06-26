"""Unit tests for S-07: Fine-tuning dataset quality filter improvements."""

from __future__ import annotations

import uuid

import pytest

from workers.fine_tuning.collect import TrainingPair
from workers.fine_tuning.quality import QualityReport, filter_pairs, score_pair


def _pair(user: str, assistant: str, conv_id: uuid.UUID | None = None) -> TrainingPair:
    return TrainingPair(
        conversation_id=conv_id or uuid.uuid4(),
        user=user,
        assistant=assistant,
    )


class TestScorePair:
    def test_ideal_ratio_scores_high(self):
        user = "Ciao, mi interessava avere maggiori informazioni sul vostro servizio"
        asst = "Certo! Posso aiutarti con tutte le informazioni necessarie sul servizio."
        p = _pair(user, asst)
        score = score_pair(p)
        assert 0.5 <= score <= 1.0

    def test_empty_user_scores_zero(self):
        assert score_pair(_pair("", "risposta")) == 0.0

    def test_very_long_assistant_scores_lower(self):
        p = _pair("ok", "a" * 5000)
        score = score_pair(p)
        assert score < 0.6

    def test_score_in_range(self):
        for user, asst in [
            ("breve", "risposta proporzionata che aiuta il cliente"),
            ("domanda?", "a"),
            ("x", "x" * 1000),
        ]:
            s = score_pair(_pair(user, asst))
            assert 0.0 <= s <= 1.0, f"Out of range for ({user!r}, {asst!r})"


class TestFilterPairs:
    def _conv(self, n_pairs: int = 3, **kwargs) -> list[TrainingPair]:
        cid = uuid.uuid4()
        return [_pair(f"user msg {i}", f"assistant reply {i}, questo è un testo abbastanza lungo per passare il filtro", cid) for i in range(n_pairs)]

    def test_basic_pass(self):
        pairs = self._conv(3)
        report = filter_pairs(pairs)
        assert len(report.kept) == 3
        assert report.dropped == 0

    def test_premature_dropoff_dropped(self):
        pairs = [_pair("u", "assistant risposta lunga abbastanza")]  # only 1 pair, < min=2
        report = filter_pairs(pairs)
        assert report.dropped == 1
        assert report.reasons.get("premature_dropoff") == 1

    def test_bot_error_drops_whole_conversation(self):
        cid = uuid.uuid4()
        pairs = [
            _pair("ciao", "si è verificato un errore durante l'elaborazione", cid),
            _pair("riprova", "risposta normale", cid),
        ]
        report = filter_pairs(pairs)
        assert len(report.kept) == 0
        assert report.reasons.get("bot_error") == 2

    def test_empty_turn_dropped(self):
        cid = uuid.uuid4()
        pairs = [
            _pair("", "risposta", cid),
            _pair("domanda valida", "risposta valida abbastanza lunga", cid),
        ]
        report = filter_pairs(pairs)
        assert report.reasons.get("empty_turn") == 1
        assert len(report.kept) == 1

    def test_bad_length_ratio_dropped(self):
        cid = uuid.uuid4()
        long_user = "messaggio molto lungo " * 50  # 1100 chars
        good_user = "Vorrei un preventivo per il vostro servizio"
        good_asst = "Certamente! Posso prepararti un preventivo personalizzato subito."
        pairs = [
            _pair(long_user, "capito!", cid),  # ratio 7/1100 ≈ 0.006 → too short
            _pair(good_user, good_asst, cid),  # ratio ≈ 1.4 → good
        ]
        report = filter_pairs(pairs)
        assert report.reasons.get("bad_length_ratio") == 1
        assert len(report.kept) == 1

    def test_near_duplicate_dedup(self):
        """Same user message prefix across two conversations → only first kept."""
        cid1, cid2 = uuid.uuid4(), uuid.uuid4()
        base_user = "Ciao, vorrei informazioni sul servizio"
        base_asst = "Certamente! Posso aiutarti con tutte le informazioni necessarie."
        pairs = [
            _pair(base_user, base_asst, cid1),
            _pair(base_user, base_asst, cid1),  # same conv — second also deduplicated
            _pair(base_user, base_asst, cid2),
            _pair(base_user + " extra", base_asst, cid2),
        ]
        report = filter_pairs(pairs)
        assert report.reasons.get("near_duplicate", 0) >= 1

    def test_deduplicate_false_keeps_all_duplicates(self):
        cid = uuid.uuid4()
        pairs = [
            _pair("stessa domanda", "stessa risposta abbastanza lunga", cid),
            _pair("stessa domanda", "stessa risposta abbastanza lunga", cid),
        ]
        report = filter_pairs(pairs, deduplicate=False)
        assert report.reasons.get("near_duplicate", 0) == 0
        assert len(report.kept) == 2
