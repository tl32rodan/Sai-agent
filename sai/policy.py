"""Politeness policy — pure, injected clock (PLAN.md §9).

Verdicts: Push(text) | Drop(reason). Reasons: "blind" | "ttl" | "cooldown" | "rate_limit".
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from .salience import AdviceCandidate

MAX_PUSH_CHARS = 80
_ROLLING_WINDOW_S = 3600.0


@dataclass(frozen=True)
class Push:
    text: str
    fingerprint: str
    rule: str
    severity: str


@dataclass(frozen=True)
class Drop:
    reason: str
    fingerprint: str
    rule: str
    severity: str


Verdict = Push | Drop


def render_push(candidate: AdviceCandidate, pull_key: str = "g") -> str:
    """One line, one volume, ≤ 80 chars (PLAN.md §10). No LLM text here, ever."""
    suffix = f" · C-b {pull_key} for why"
    body = candidate.evidence
    budget = MAX_PUSH_CHARS - len("sai ▸ ") - len(suffix)
    if len(body) > budget:
        body = body[: budget - 1] + "…"
    return f"sai ▸ {body}{suffix}"


class TokenBucket:
    """Starts full: the first useful ping must not wait 20 minutes."""

    def __init__(self, capacity: int, refill_period_s: float, clock: Callable[[], float]):
        self._capacity = capacity
        self._refill_period_s = refill_period_s
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()

    def try_take(self) -> bool:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(float(self._capacity), self._tokens + elapsed / self._refill_period_s)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class Policy:
    """Boundary-only timing is structural (the sensor only emits completed
    commands); this class enforces cooldown, TTL, blind, and the rate limit."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        cooldown_s: float = 15 * 60,
        bucket_capacity: int = 3,
        bucket_refill_s: float = 20 * 60,
        ttl_s: float = 10 * 60,
        pull_key: str = "g",
    ):
        self._clock = clock
        self._cooldown_s = cooldown_s
        self._capacity = bucket_capacity
        self._ttl_s = ttl_s
        self._pull_key = pull_key
        self._bucket = TokenBucket(bucket_capacity, bucket_refill_s, clock)
        self._last_push_by_fp: dict[str, float] = {}
        self._push_times: deque[float] = deque()

    def decide(self, candidate: AdviceCandidate, *, in_blind: bool = False) -> Verdict:
        now = self._clock()

        def drop(reason: str) -> Drop:
            return Drop(reason, candidate.fingerprint, candidate.rule, candidate.severity)

        # Blind events never become candidates upstream; asserted here too (§9).
        if in_blind:
            return drop("blind")
        # TTL — a stale hint is worse than silence.
        if now - candidate.t > self._ttl_s:
            return drop("ttl")
        # Per-fingerprint cooldown.
        last = self._last_push_by_fp.get(candidate.fingerprint)
        if last is not None and now - last < self._cooldown_s:
            return drop("cooldown")
        # Rate limit. Invariant 2 demands ≤ capacity pushes in ANY rolling hour,
        # which a full-at-start bucket alone would not guarantee (burst + refill);
        # enforce both — the stricter wins (PLAN.md §15.1 Q5).
        while self._push_times and now - self._push_times[0] >= _ROLLING_WINDOW_S:
            self._push_times.popleft()
        if len(self._push_times) >= self._capacity:
            return drop("rate_limit")
        if not self._bucket.try_take():
            return drop("rate_limit")

        self._last_push_by_fp[candidate.fingerprint] = now
        self._push_times.append(now)
        return Push(
            render_push(candidate, self._pull_key),
            candidate.fingerprint, candidate.rule, candidate.severity,
        )
