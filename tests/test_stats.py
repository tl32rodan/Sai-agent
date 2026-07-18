from __future__ import annotations

import time

import pytest

from sai.stats import compute_stats, render_stats
from tests.conftest import T0


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    time.tzset()


def fixture_records() -> list[dict]:
    return [
        {"t": T0, "type": "cmd", "cmd": "make lens", "exit": 2, "fp": "f1" * 6},
        {"t": T0 + 1, "type": "verdict", "verdict": "push", "rule": "r1",
         "fp": "f1" * 6, "text": "sai ▸ exit 2: make · C-b g for why"},
        {"t": T0 + 30, "type": "pull"},                        # within 2 min of the push
        {"t": T0 + 600, "type": "blind", "pane": "%1", "state": "enter"},
        {"t": T0 + 1200, "type": "blind", "pane": "%1", "state": "exit"},
        {"t": T0 + 2000, "type": "cmd", "cmd": "make lens", "exit": 2, "fp": "f1" * 6},
        {"t": T0 + 2001, "type": "verdict", "verdict": "drop", "reason": "cooldown", "rule": "r2"},
        {"t": T0 + 3000, "type": "pull"},                      # NOT within 2 min of a push
        {"t": T0 + 3600, "type": "cmd", "cmd": "rm -rf x", "exit": 1, "fp": "f2" * 6},
    ]


class TestComputeStats:
    def test_counts(self):
        stats = compute_stats(fixture_records(), now=T0 + 4000)
        assert stats["pushes"] == 1
        assert stats["pushes_by_rule"] == {"r1": 1}
        assert stats["drops"] == 1
        assert stats["drops_by_reason"] == {"cooldown": 1}
        assert stats["pulls"] == 2
        assert stats["pulls_after_push"] == 1

    def test_top_fingerprints_by_failure_count(self):
        stats = compute_stats(fixture_records(), now=T0 + 4000)
        assert stats["top_fingerprints"][0] == ("f1" * 6, 2, "make lens")
        assert stats["top_fingerprints"][1] == ("f2" * 6, 1, "rm -rf x")

    def test_coverage_approximation(self):
        stats = compute_stats(fixture_records(), now=T0 + 4000)
        (_day, coverage, active_s, blind_s), = stats["coverage_by_day"]
        assert active_s == 3600.0
        assert blind_s == 600.0
        assert coverage == pytest.approx(1 - 600 / 3600)

    def test_unclosed_blind_span_counts_to_last_record(self):
        records = [
            {"t": T0, "type": "cmd", "cmd": "ls", "exit": 0, "fp": "f3" * 6},
            {"t": T0 + 100, "type": "blind", "pane": "%1", "state": "enter"},
            {"t": T0 + 400, "type": "cmd", "cmd": "ls", "exit": 0, "fp": "f3" * 6},
        ]
        (_day, coverage, active_s, blind_s), = compute_stats(
            records, now=T0 + 500)["coverage_by_day"]
        assert (active_s, blind_s) == (400.0, 300.0)

    def test_days_filter(self):
        old = {"t": T0 - 3 * 86400, "type": "pull"}
        stats = compute_stats([old, *fixture_records()], now=T0 + 4000, days=1)
        assert stats["pulls"] == 2

    def test_empty_records(self):
        stats = compute_stats([], now=T0)
        assert stats["pushes"] == 0 and stats["coverage_by_day"] == []


class TestRenderStats:
    def test_all_sections_present(self):
        out = render_stats(compute_stats(fixture_records(), now=T0 + 4000))
        assert "pushes: 1" in out and "r1 1" in out
        assert "drops:  1" in out and "cooldown 1" in out
        assert "pull-after-push: 1/1 (100%)" in out
        assert "coverage" in out and "83%" in out and "approx" in out
        assert "f1f1f1f1f1f1  ×2  make lens" in out

    def test_no_data_yet(self):
        out = render_stats(compute_stats([], now=T0), days=7)
        assert "last 7 day(s)" in out
        assert "no data yet" in out and "n/a (no pushes)" in out
