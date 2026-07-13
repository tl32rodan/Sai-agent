from __future__ import annotations

from sai.policy import MAX_PUSH_CHARS, Drop, Policy, Push, TokenBucket, render_push
from sai.salience import AdviceCandidate, HINT
from tests.conftest import T0, FakeClock


def make_candidate(fp: str = "aaaabbbbcccc", t: float = T0, evidence: str = "exit 2: make"):
    return AdviceCandidate(fp, HINT, "r1", evidence, t, "%3")


def make_policy(clock, **overrides) -> Policy:
    defaults = dict(
        clock=clock, cooldown_s=900.0, bucket_capacity=3,
        bucket_refill_s=1200.0, ttl_s=600.0, pull_key="g",
    )
    return Policy(**{**defaults, **overrides})


class TestRenderPush:
    def test_matches_plan_example_shape(self):
        cand = make_candidate(evidence="exit 2 ×3 — same error repeating")
        assert render_push(cand, "g") == "sai ▸ exit 2 ×3 — same error repeating · C-b g for why"

    def test_never_exceeds_80_chars(self):
        cand = make_candidate(evidence="x" * 300)
        assert len(render_push(cand, "g")) <= MAX_PUSH_CHARS

    def test_configured_key_appears(self):
        assert "C-b y for why" in render_push(make_candidate(), "y")


class TestTokenBucket:
    def test_burst_up_to_capacity_then_deny(self):
        bucket = TokenBucket(3, 1200.0, FakeClock())
        assert [bucket.try_take() for _ in range(4)] == [True, True, True, False]

    def test_refills_one_token_per_period(self):
        clock = FakeClock()
        bucket = TokenBucket(1, 1200.0, clock)
        assert bucket.try_take() and not bucket.try_take()
        clock.advance(1200)
        assert bucket.try_take()

    def test_refill_capped_at_capacity(self):
        clock = FakeClock()
        bucket = TokenBucket(2, 100.0, clock)
        clock.advance(10_000)  # long idle must not bank more than capacity
        assert [bucket.try_take() for _ in range(3)] == [True, True, False]


class TestPolicyDecide:
    def test_fresh_candidate_pushes(self):
        clock = FakeClock()
        verdict = make_policy(clock).decide(make_candidate())
        assert isinstance(verdict, Push)
        assert verdict.text.startswith("sai ▸ ")
        assert (verdict.fingerprint, verdict.rule) == ("aaaabbbbcccc", "r1")

    def test_blind_dropped_even_if_otherwise_pushable(self):
        clock = FakeClock()
        verdict = make_policy(clock).decide(make_candidate(), in_blind=True)
        assert verdict == Drop("blind", "aaaabbbbcccc", "r1", HINT)

    def test_stale_candidate_dropped(self):
        clock = FakeClock(T0 + 601)
        verdict = make_policy(clock).decide(make_candidate(t=T0))
        assert isinstance(verdict, Drop) and verdict.reason == "ttl"

    def test_candidate_at_ttl_boundary_still_pushes(self):
        clock = FakeClock(T0 + 600)
        assert isinstance(make_policy(clock).decide(make_candidate(t=T0)), Push)

    def test_cooldown_silences_same_fingerprint(self):
        clock = FakeClock()
        policy = make_policy(clock)
        assert isinstance(policy.decide(make_candidate()), Push)
        clock.advance(60)
        verdict = policy.decide(make_candidate(t=clock.t))
        assert isinstance(verdict, Drop) and verdict.reason == "cooldown"

    def test_cooldown_expires(self):
        clock = FakeClock()
        policy = make_policy(clock)
        assert isinstance(policy.decide(make_candidate()), Push)
        clock.advance(901)
        assert isinstance(policy.decide(make_candidate(t=clock.t)), Push)

    def test_different_fingerprints_not_cooled_down(self):
        clock = FakeClock()
        policy = make_policy(clock)
        assert isinstance(policy.decide(make_candidate(fp="a" * 12)), Push)
        assert isinstance(policy.decide(make_candidate(fp="b" * 12)), Push)

    def test_bucket_exhaustion_drops(self):
        clock = FakeClock()
        policy = make_policy(clock)
        for i in range(3):
            assert isinstance(policy.decide(make_candidate(fp=f"{i:012d}")), Push)
        verdict = policy.decide(make_candidate(fp="d" * 12))
        assert isinstance(verdict, Drop) and verdict.reason == "rate_limit"

    def test_rolling_hour_cap_outlasts_bucket_refill(self):
        # 3 pushes at t0; 25 min later the bucket has a token again, but the
        # rolling-hour cap (invariant 2) still says no (PLAN.md §15.1 Q5).
        clock = FakeClock()
        policy = make_policy(clock)
        for i in range(3):
            assert isinstance(policy.decide(make_candidate(fp=f"{i:012d}")), Push)
        clock.advance(1500)
        verdict = policy.decide(make_candidate(fp="d" * 12, t=clock.t))
        assert isinstance(verdict, Drop) and verdict.reason == "rate_limit"

    def test_pushes_resume_after_rolling_hour(self):
        clock = FakeClock()
        policy = make_policy(clock)
        for i in range(3):
            assert isinstance(policy.decide(make_candidate(fp=f"{i:012d}")), Push)
        clock.advance(3601)
        assert isinstance(policy.decide(make_candidate(fp="d" * 12, t=clock.t)), Push)

    def test_drop_does_not_consume_budget(self):
        clock = FakeClock()
        policy = make_policy(clock)
        assert isinstance(policy.decide(make_candidate()), Push)
        clock.advance(60)
        for _ in range(10):  # cooldown drops must not eat tokens or window slots
            policy.decide(make_candidate(t=clock.t))
        assert isinstance(policy.decide(make_candidate(fp="e" * 12, t=clock.t)), Push)
        assert isinstance(policy.decide(make_candidate(fp="f" * 12, t=clock.t)), Push)
