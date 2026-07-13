"""Event schema, TSV → canonical normalization, ring buffer, blind-zone tracking.

Pure module (PLAN.md §5): no IO, no clock. All timestamps arrive as arguments —
the daemon stamps pane output on read and passes the times in.
"""
from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass, replace
import re
from typing import Iterable

# CSI / OSC / short escape sequences (SGR colors incl. colon subparameters,
# cursor movement, titles, keypad modes like ESC= / ESC>, charsets like ESC(B).
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-9;:?<=>]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[ -/]*[0-~])"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """Remove escape sequences, apply backspaces (zle echo artifacts), and
    drop remaining control characters (except tab)."""
    text = _ANSI_RE.sub("", text)
    if "\b" in text:
        chars: list[str] = []
        for ch in text:
            if ch == "\b":
                if chars:
                    chars.pop()
            else:
                chars.append(ch)
        text = "".join(chars)
    return _CTRL_RE.sub("", text)


# Alternate-screen toggles: modern ?1049, legacy ?47 / ?1047 (PLAN.md §6).
_BLIND_RE = re.compile(r"\x1b\[\?(?:1049|1047|47)([hl])")


@dataclass(frozen=True)
class CmdEvent:
    """A completed command. `t` is the completion epoch (shell-side)."""

    t: float
    cmd: str
    cwd: str
    exit: int
    dur_s: float
    pane: str
    tail: tuple[str, ...] = ()

    @property
    def start(self) -> float:
        return self.t - self.dur_s


@dataclass(frozen=True)
class BlindEvent:
    t: float
    pane: str
    state: str  # "enter" | "exit"


def parse_tsv_line(line: str) -> CmdEvent | None:
    """Parse one shell-side TSV record; None for malformed input (never raises).

    Fields: epoch_float, kind, exit, dur_s, cwd, pane_id, b64(cmd).
    """
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) != 7 or parts[1] != "cmd":
        return None
    epoch, _kind, exit_s, dur_s, cwd, pane, b64 = parts
    try:
        cmd = base64.b64decode(b64, validate=True).decode("utf-8", "replace")
        return CmdEvent(
            t=float(epoch), cmd=cmd, cwd=cwd, exit=int(exit_s),
            dur_s=max(0.0, float(dur_s)), pane=pane,
        )
    except (ValueError, binascii.Error):
        return None


def with_tail(event: CmdEvent, tail: Iterable[str]) -> CmdEvent:
    return replace(event, tail=tuple(tail))


def cmd_to_json(event: CmdEvent, fingerprint: str) -> dict:
    """Canonical JSONL form (PLAN.md §6). `fp` keeps the north-star recurrence
    metric computable later without re-deriving fingerprints (§2 teachable moments)."""
    return {
        "t": event.t, "type": "cmd", "cmd": event.cmd, "cwd": event.cwd,
        "exit": event.exit, "dur_s": event.dur_s, "pane": event.pane,
        "tail": list(event.tail), "fp": fingerprint,
    }


def blind_to_json(event: BlindEvent) -> dict:
    return {"t": event.t, "type": "blind", "pane": event.pane, "state": event.state}


class RingBuffer:
    """Fixed-capacity ring of (arrival_t, line); arrival times stamped by the caller."""

    def __init__(self, capacity: int = 500):
        self._lines: deque[tuple[float, str]] = deque(maxlen=capacity)

    def append(self, t: float, line: str) -> None:
        self._lines.append((t, line))

    def snapshot(
        self, start: float, end: float, *,
        slack_s: float = 0.5, max_lines: int = 40, max_bytes: int = 8192,
    ) -> tuple[str, ...]:
        """Lines arriving in [start, end + slack], most recent `max_lines`, capped
        at `max_bytes` total, ANSI-stripped (PLAN.md §6)."""
        window = [
            strip_ansi(line).rstrip()
            for t, line in self._lines
            if start <= t <= end + slack_s
        ]
        window = window[-max_lines:]
        kept: list[str] = []
        total = 0
        for line in reversed(window):  # keep the most recent lines under the byte cap
            total += len(line.encode("utf-8", "surrogateescape")) + 1
            if total > max_bytes:
                break
            kept.append(line)
        if not kept and window:  # a single oversized line: truncate, don't lose it
            raw = window[-1].encode("utf-8", "surrogateescape")[:max_bytes]
            kept = [raw.decode("utf-8", "ignore")]
        return tuple(reversed(kept))


class PaneTracker:
    """Per-pane state: blind flag + ring buffer + partial-line assembly.

    feed() consumes a raw output chunk (already decoded to str) stamped with its
    arrival time, updates the ring with complete non-blind lines, and returns the
    blind-state transitions ("enter"/"exit") seen in the chunk, in order.
    Output inside blind spans is excluded from the ring entirely (PLAN.md §6).
    """

    def __init__(self, ring_lines: int = 500):
        self.blind = False
        self.ring = RingBuffer(ring_lines)
        self._partial = ""
        self._esc_carry = ""
        self._skip_lf = False

    def feed(self, t: float, data: str) -> list[str]:
        data = self._esc_carry + data
        self._esc_carry = ""
        # Hold back a CSI sequence cut by the chunk boundary so a split
        # alternate-screen toggle is still seen whole on the next feed.
        if m := re.search(r"\x1b(?:\[[0-9;:?<=>]*)?$", data):
            self._esc_carry = data[m.start():]
            data = data[: m.start()]
        transitions: list[str] = []
        pos = 0
        for m in _BLIND_RE.finditer(data):
            self._ingest(t, data[pos:m.start()])
            pos = m.end()
            entering = m.group(1) == "h"
            if entering != self.blind:
                self.blind = entering
                transitions.append("enter" if entering else "exit")
                if entering:
                    self._partial = ""  # a line cut by a blind entry is unusable
                    self._skip_lf = False
        self._ingest(t, data[pos:])
        return transitions

    def _ingest(self, t: float, text: str) -> None:
        if not text or self.blind:
            return
        if self._skip_lf:  # \r\n split across chunks: the \r already broke the line
            self._skip_lf = False
            if text.startswith("\n"):
                text = text[1:]
                if not text:
                    return
        self._skip_lf = text.endswith("\r")
        self._partial += text.replace("\r\n", "\n").replace("\r", "\n")
        *complete, self._partial = self._partial.split("\n")
        for line in complete:
            self.ring.append(t, line)
