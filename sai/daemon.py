"""IO shell (PLAN.md §5): tail files, stamp arrival times, decide, speak via tmux.

DaemonCore holds the orchestration with injected clock/push/log so the golden
replay test can drive it without touching the filesystem or tmux; Daemon is the
thin file-tailing wrapper around it.
"""
from __future__ import annotations

import codecs
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from collections import deque

from .config import Config
from .events import (
    BlindEvent, CmdEvent, PaneTracker, blind_to_json, cmd_to_json,
    parse_tsv_line, with_tail,
)
from .fingerprint import fingerprint
from .policy import Policy, Push, Verdict
from .salience import STRUGGLE_WINDOW, evaluate
from .status import render_status

POLL_S = 0.2


def verdict_to_json(verdict: Verdict, t: float, pane: str) -> dict:
    record = {
        "t": t, "type": "verdict", "pane": pane, "fp": verdict.fingerprint,
        "rule": verdict.rule, "severity": verdict.severity,
    }
    if isinstance(verdict, Push):
        record |= {"verdict": "push", "text": verdict.text}
    else:
        record |= {"verdict": "drop", "reason": verdict.reason}
    return record


class DaemonCore:
    """Pure-ish pipeline: pane output + TSV lines in, log records + pushes out."""

    def __init__(
        self,
        config: Config,
        *,
        clock: Callable[[], float],
        push: Callable[[str, str], None],
        log: Callable[[dict], None],
    ):
        self._config = config
        self._clock = clock
        self._push = push
        self._log = log
        self._trackers: dict[str, PaneTracker] = {}
        self._history: dict[str, list[CmdEvent]] = {}
        self._recent: deque[CmdEvent] = deque(maxlen=10)  # cross-pane, for status
        self._last_pane = ""
        self._policy = Policy(
            clock=clock,
            cooldown_s=config.cooldown_min * 60,
            bucket_capacity=config.bucket_capacity,
            bucket_refill_s=config.bucket_refill_min * 60,
            ttl_s=config.ttl_min * 60,
            pull_key=config.pull_key,
        )

    def on_pane_output(self, pane: str, data: str) -> None:
        now = self._clock()
        self._last_pane = pane
        tracker = self._trackers.setdefault(pane, PaneTracker(self._config.ring_lines))
        for state in tracker.feed(now, data):
            self._log(blind_to_json(BlindEvent(now, pane, state)))

    def on_tsv_line(self, line: str) -> None:
        event = parse_tsv_line(line)
        if event is None:
            return
        now = self._clock()
        self._last_pane = event.pane
        tracker = self._trackers.get(event.pane)
        if tracker is not None:
            event = with_tail(event, tracker.ring.snapshot(
                event.start, event.t,
                max_lines=self._config.tail_lines, max_bytes=self._config.tail_bytes,
            ))
        fp = fingerprint(event.cmd, event.tail)
        self._log(cmd_to_json(event, fp))

        if tracker is not None and tracker.blind:
            # Invariant 3: an event inside a blind span never becomes a candidate.
            # Salience is not consulted — not even as R2 history later (§6:
            # blind spans are excluded from salience entirely) — but the
            # suppression is still audited (§9).
            self._log({
                "t": now, "type": "verdict", "pane": event.pane, "fp": fp,
                "rule": None, "severity": None, "verdict": "drop", "reason": "blind",
            })
            return

        candidate = evaluate(event, self._history.get(event.pane, []))
        if candidate is not None:
            verdict = self._policy.decide(candidate)
            self._log(verdict_to_json(verdict, now, event.pane))
            if isinstance(verdict, Push):
                self._push(event.pane, verdict.text)

        history = self._history.setdefault(event.pane, [])
        history.append(event)
        del history[:-STRUGGLE_WINDOW]
        self._recent.append(event)

    def status_text(self) -> str:
        """Two-line ambient status (§16.2) — glanceable, zero LLM."""
        tracker = self._trackers.get(self._last_pane)
        return render_status(
            tuple(self._recent),
            now=self._clock(),
            blind=tracker.blind if tracker else False,
            pull_key=self._config.pull_key,
        )


class _Tail:
    """Byte-offset tailer; resets on truncation/rotation.

    start_at_end skips content already present when the tailer is created —
    a restarted daemon must not replay history (duplicate audit records,
    re-pushes of stale events)."""

    def __init__(self, path: Path, *, start_at_end: bool = False):
        self.path = path
        self.offset = 0
        if start_at_end:
            try:
                self.offset = path.stat().st_size
            except FileNotFoundError:
                pass

    def read(self) -> bytes:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.offset = 0
            return b""
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return b""
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()
        return data


def tmux_push(pane: str, text: str) -> None:
    # ## renders a literal # — observed command text must never be
    # format-expanded (display-message -l would be cleaner but needs tmux 3.4;
    # the floor is 3.2).
    cmd = ["tmux", "display-message"]
    if pane.startswith("%"):
        cmd += ["-t", pane]
    subprocess.run(cmd + [text.replace("#", "##")], check=False, capture_output=True)


class Daemon:
    """Tails events.tsv and out-%N.log files in the state dir (poll ≈ 0.2 s)."""

    def __init__(
        self,
        state_dir: Path,
        config: Config,
        *,
        clock: Callable[[], float] = time.time,
        push: Callable[[str, str], None] = tmux_push,
        poll_s: float = POLL_S,
        runtime_dir: Path | None = None,
    ):
        state_dir.mkdir(parents=True, exist_ok=True)
        run_dir = runtime_dir or state_dir  # volatile files: host-local in prod
        run_dir.mkdir(parents=True, exist_ok=True)
        self._state_dir = state_dir
        self._pings = state_dir / "pings.jsonl"
        self._poll_s = poll_s
        self.core = DaemonCore(config, clock=clock, push=push, log=self._append_log)
        # Pre-existing content is history, not news: start at EOF. Files that
        # appear after startup are read from the beginning.
        self._tsv_tail = _Tail(state_dir / "events.tsv", start_at_end=True)
        self._tsv_buf = b""
        self._pane_tails: dict[str, _Tail] = {
            path.name[len("out-"):-len(".log")]: _Tail(path, start_at_end=True)
            for path in state_dir.glob("out-*.log")
        }
        self._decoders: dict[str, codecs.IncrementalDecoder] = {}
        self._status_path = run_dir / "status"
        self._last_status: str | None = None

    def _append_log(self, record: dict) -> None:
        with self._pings.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def step(self) -> None:
        # Panes before TSV: a command's final output must be in the ring before
        # its completion record is snapshotted (§6 tail window).
        for path in sorted(self._state_dir.glob("out-*.log")):
            pane = path.name[len("out-"):-len(".log")]
            tail = self._pane_tails.setdefault(pane, _Tail(path))
            data = tail.read()
            if data:
                # incremental decode: a UTF-8 char split across polls must not
                # become U+FFFD (it would destabilize fingerprints)
                decoder = self._decoders.setdefault(
                    pane, codecs.getincrementaldecoder("utf-8")(errors="replace")
                )
                text = decoder.decode(data)
                if text:
                    self.core.on_pane_output(pane, text)
        data = self._tsv_tail.read()
        if data:
            self._tsv_buf += data
            *complete, self._tsv_buf = self._tsv_buf.split(b"\n")
            for raw in complete:
                if raw:
                    self.core.on_tsv_line(raw.decode("utf-8", "replace"))
        status = self.core.status_text()
        if status != self._last_status:  # rewrites on events + minute ticks only
            self._status_path.write_text(status + "\n")
            self._last_status = status

    def run(self) -> None:
        while True:
            self.step()
            time.sleep(self._poll_s)
