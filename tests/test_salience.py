from __future__ import annotations

from sai.salience import HINT, WARN, evaluate
from tests.conftest import T0, make_event


class TestR1PlainFailure:
    def test_nonzero_exit_is_hint(self):
        cand = evaluate(make_event(exit=2, cmd="make lens"), [])
        assert cand is not None
        assert (cand.rule, cand.severity) == ("r1", HINT)
        assert "exit 2" in cand.evidence and "make" in cand.evidence

    def test_success_with_clean_tail_is_silent(self):
        assert evaluate(make_event(exit=0, tail=("all good",)), []) is None

    def test_candidate_carries_event_time_and_pane(self):
        cand = evaluate(make_event(t=T0, exit=1, pane="%7"), [])
        assert (cand.t, cand.pane) == (T0, "%7")


class TestR2StruggleLoop:
    def test_two_similar_failures_warn(self):
        history = [make_event(t=T0 - 60, exit=2, cmd="make lens")]
        cand = evaluate(make_event(t=T0, exit=2, cmd="make lens"), history)
        assert (cand.rule, cand.severity) == ("r2", WARN)
        assert "×2" in cand.evidence

    def test_similar_but_not_identical_commands(self):
        history = [make_event(t=T0 - 60, exit=1, cmd="pytest tests/test_a.py")]
        cand = evaluate(make_event(t=T0, exit=1, cmd="pytest tests/test_a.py -k foo"), history)
        assert cand.rule == "r2"

    def test_dissimilar_failures_stay_r1(self):
        history = [make_event(t=T0 - 60, exit=1, cmd="git push origin main")]
        cand = evaluate(make_event(t=T0, exit=2, cmd="make lens", tail=()), history)
        assert cand.rule == "r1"

    def test_current_success_never_struggles(self):
        # a loop that just ended in success needs no ping (PLAN.md §15.1 Q6)
        history = [
            make_event(t=T0 - 120, exit=2, cmd="make lens"),
            make_event(t=T0 - 60, exit=2, cmd="make lens"),
        ]
        assert evaluate(make_event(t=T0, exit=0, cmd="make lens"), history) is None

    def test_window_limited_to_last_five_commands(self):
        old_failure = make_event(t=T0 - 600, exit=2, cmd="make lens")
        padding = [
            make_event(t=T0 - 500 + i, exit=0, cmd=f"okcmd{i}") for i in range(4)
        ]
        cand = evaluate(make_event(t=T0, exit=2, cmd="make lens"), [old_failure, *padding])
        assert cand.rule == "r1"  # the similar failure fell out of the 5-command window

    def test_struggle_beats_r3(self):
        tail = ("make: /x: No such file or directory",)
        history = [make_event(t=T0 - 60, exit=2, cmd="make lens", tail=tail)]
        cand = evaluate(make_event(t=T0, exit=2, cmd="make lens", tail=tail), history)
        assert (cand.rule, cand.severity) == ("r2", WARN)


class TestR3ErrorPatterns:
    PATTERN_LINES = [
        "Traceback (most recent call last):",
        "open: Permission denied",
        "zsh: command not found: frob",
        "undefined reference to `main'",
        "Segmentation fault (core dumped)",
        "cat: x: No such file or directory",
    ]

    def test_each_pattern_hints_on_success(self):
        for line in self.PATTERN_LINES:
            cand = evaluate(make_event(exit=0, tail=(line,)), [])
            assert cand is not None and (cand.rule, cand.severity) == ("r3", HINT), line

    def test_pattern_plus_failure_warns(self):
        cand = evaluate(
            make_event(exit=1, tail=("Traceback (most recent call last):",)), []
        )
        assert (cand.rule, cand.severity) == ("r3", WARN)
        assert "exit 1" in cand.evidence

    def test_unknown_error_text_does_not_match(self):
        cand = evaluate(make_event(exit=0, tail=("something exploded",)), [])
        assert cand is None
