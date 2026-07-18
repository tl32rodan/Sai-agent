"""Three pure salience rules — zero LLM in the push path (PLAN.md §8)."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Sequence

from .events import CmdEvent
from .fingerprint import fingerprint

HINT = "hint"
WARN = "warn"

ERROR_PATTERNS = re.compile(
    r"Traceback|Permission denied|command not found"
    r"|undefined reference|Segmentation fault|No such file"
)

STRUGGLE_WINDOW = 5        # R2: look at the last 5 commands…
STRUGGLE_FAILURES = 2      # …for at least 2 failures…
STRUGGLE_SIMILARITY = 0.6  # …whose commands are pairwise-similar at least this much.


@dataclass(frozen=True)
class AdviceCandidate:
    fingerprint: str
    severity: str  # HINT | WARN
    rule: str      # "r1" | "r2" | "r3"
    evidence: str  # short human phrase; doubles as the push-message body (§10)
    t: float
    pane: str


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= STRUGGLE_SIMILARITY


def _cmd_head(cmd: str) -> str:
    tokens = cmd.split()
    return tokens[0] if tokens else "?"


def evaluate(event: CmdEvent, history: Sequence[CmdEvent]) -> AdviceCandidate | None:
    """history: prior completed commands for the same pane, oldest first.

    Rule priority when several fire: R2 (struggle) > R3+R1 > R3 > R1 — the most
    specific evidence wins; severity never decreases by combining.
    """
    failed = event.exit != 0
    error_match = ERROR_PATTERNS.search("\n".join(event.tail))
    fp = fingerprint(event.cmd, event.tail)

    # R2 — struggle loop. The current event must itself be a failure: a loop
    # that just ended in success needs no ping (PLAN.md §15.1 Q6).
    window = [*history[-(STRUGGLE_WINDOW - 1):], event]
    failures = [e for e in window if e.exit != 0]
    if (
        failed
        and len(failures) >= STRUGGLE_FAILURES
        and any(
            _similar(a.cmd, b.cmd)
            for i, a in enumerate(failures)
            for b in failures[i + 1:]
        )
    ):
        evidence = f"exit {event.exit} ×{len(failures)} — same error repeating"
        return AdviceCandidate(fp, WARN, "r2", evidence, event.t, event.pane)

    # R3 — known error pattern in the tail; WARN when combined with a failure (R1).
    if error_match and failed:
        evidence = f"{error_match.group(0)}: {_cmd_head(event.cmd)} (exit {event.exit})"
        return AdviceCandidate(fp, WARN, "r3", evidence, event.t, event.pane)
    if error_match:
        evidence = f"{error_match.group(0)} in output: {_cmd_head(event.cmd)}"
        return AdviceCandidate(fp, HINT, "r3", evidence, event.t, event.pane)

    # R1 — plain failure.
    if failed:
        evidence = f"exit {event.exit}: {_cmd_head(event.cmd)}"
        return AdviceCandidate(fp, HINT, "r1", evidence, event.t, event.pane)

    return None
