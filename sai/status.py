"""Ambient status surface (PLAN.md §16.2) — pure module.

Two lines, double inverted pyramid: line 1 = what the user is doing (the
conclusion), line 2 = what Sai could do next (the offer). Deep analysis stays
lazy behind the pull. Glanceable, not interruptive — this surface bypasses the
politeness policy because looking at it costs the user nothing.
"""
from __future__ import annotations

from typing import Sequence

from .events import CmdEvent
from .fingerprint import fingerprint

MAX_LINE_CHARS = 80
RECENT_WINDOW = 5


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[: MAX_LINE_CHARS - 1] + "…"


def _head(cmd: str) -> str:
    tokens = cmd.split()
    return tokens[0] if tokens else "?"


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def render_status(
    events: Sequence[CmdEvent], *, now: float, blind: bool = False, pull_key: str = "g",
) -> str:
    """Render the two-line status from recent completed commands (oldest first)."""
    line1, line2 = _lines(list(events), now, blind, pull_key)
    return _clip(line1) + "\n" + _clip(line2)


def _lines(events: list[CmdEvent], now: float, blind: bool, pull_key: str) -> tuple[str, str]:
    if not events:
        return ("⏺ watching — nothing observed yet", f"▷ C-b {pull_key} anytime")

    last = events[-1]
    ago = _ago(now - last.t)

    if blind:
        return (
            f"⏺ in a full-screen app · last: {_head(last.cmd)} {ago}",
            "▷ blind zone — sai neither sees nor speaks here",
        )

    recent = events[-RECENT_WINDOW:]
    if last.exit != 0:
        fp = fingerprint(last.cmd, last.tail)
        repeats = sum(
            1 for e in recent if e.exit != 0 and fingerprint(e.cmd, e.tail) == fp
        )
        streak = f" ×{repeats} same error" if repeats >= 2 else ""
        line1 = f"⏺ {_head(last.cmd)} — exit {last.exit}{streak} · {ago}"
        if repeats >= 2:
            line2 = f"▷ C-b {pull_key} — this error repeated ×{repeats}: root cause?"
        else:
            line2 = f"▷ C-b {pull_key} — why did {_head(last.cmd)} fail"
        return (line1, line2)

    line1 = f"⏺ {_head(last.cmd)} ok · {ago}"
    if any(e.exit != 0 for e in recent[:-1]):
        return (line1, f"▷ back on track · C-b {pull_key} to consolidate the why")
    return (line1, f"▷ all quiet · C-b {pull_key} anytime")
