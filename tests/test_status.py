from __future__ import annotations

from sai.status import render_status
from tests.conftest import T0, make_event


def lines(text: str) -> list[str]:
    return text.split("\n")


class TestRenderStatus:
    def test_always_exactly_two_lines_under_80_chars(self):
        cases = [
            [],
            [make_event(exit=0)],
            [make_event(exit=2)],
            [make_event(cmd="x" * 300, exit=2, tail=("y" * 300,))],
        ]
        for events in cases:
            out = lines(render_status(events, now=T0 + 60))
            assert len(out) == 2
            assert all(len(line) <= 80 for line in out)

    def test_nothing_observed_yet(self):
        out = render_status([], now=T0)
        assert "watching" in out and "C-b g anytime" in out

    def test_failure_names_command_and_offers_why(self):
        out = render_status([make_event(cmd="make lens", exit=2)], now=T0 + 30)
        line1, line2 = lines(out)
        assert "make" in line1 and "exit 2" in line1 and "just now" in line1
        assert "C-b g" in line2 and "why" in line2

    def test_repeated_failure_becomes_root_cause_offer(self):
        events = [
            make_event(t=T0 - 60, cmd="make lens", exit=2, tail=("Error: boom",)),
            make_event(t=T0, cmd="make lens", exit=2, tail=("Error: boom",)),
        ]
        out = render_status(events, now=T0 + 10)
        assert "×2 same error" in out and "root cause" in out

    def test_success_after_failure_is_back_on_track(self):
        events = [
            make_event(t=T0 - 60, cmd="make lens", exit=2),
            make_event(t=T0, cmd="make lens", exit=0),
        ]
        out = render_status(events, now=T0 + 10)
        assert "ok" in out and "back on track" in out

    def test_quiet_success_is_all_quiet(self):
        out = render_status([make_event(exit=0)], now=T0 + 10)
        assert "all quiet" in out

    def test_blind_state_shown_without_pretending_to_see(self):
        out = render_status([make_event(cmd="vim x", exit=0)], now=T0 + 120, blind=True)
        assert "full-screen" in out and "blind" in out

    def test_ago_granularity(self):
        assert "just now" in render_status([make_event(exit=0)], now=T0 + 30)
        assert "5m ago" in render_status([make_event(exit=0)], now=T0 + 300)
        assert "2.0h ago" in render_status([make_event(exit=0)], now=T0 + 7200)

    def test_configured_pull_key(self):
        out = render_status([make_event(exit=2)], now=T0, pull_key="y")
        assert "C-b y" in out
