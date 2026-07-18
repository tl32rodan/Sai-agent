"""Sentry-style error grouping in miniature (PLAN.md §7). Pure module."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Sequence

from .events import strip_ansi

# Matches the §8 error patterns plus generic error/fatal/fail markers; used only
# to pick which tail line represents the failure (PLAN.md §15.1 Q7).
_ERROR_LINE_RE = re.compile(
    r"(?i)error|traceback|permission denied|command not found"
    r"|undefined reference|segmentation fault|no such file|fatal|fail"
)

# Substitution order matters: addresses before generic integers, timestamps and
# paths before either could be split by the other.
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?"  # ISO dates, with time
    r"|\b\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\b"                   # wall-clock times
)
_ABS_PATH_RE = re.compile(r"(?<![\w.])~?/[\w.@+~-]+(?:/[\w.@+~-]+)*/?")
_PID_RE = re.compile(r"(?i)\b(pid[ =:]*)\d+|\[\d+\]")
_LONG_INT_RE = re.compile(r"\d{4,}")


def _normalize(line: str) -> str:
    line = _HEX_ADDR_RE.sub("<addr>", line)
    line = _TIMESTAMP_RE.sub("<time>", line)
    line = _ABS_PATH_RE.sub("<path>", line)
    line = _PID_RE.sub(lambda m: (m.group(1) or "[") + "<num>" + ("" if m.group(1) else "]"), line)
    line = _LONG_INT_RE.sub("<num>", line)
    return " ".join(line.split())


def first_error_line(tail: Sequence[str]) -> str:
    for line in tail:
        if _ERROR_LINE_RE.search(line):
            return line
    for line in reversed(tail):
        if line.strip():
            return line
    return ""


def fingerprint(cmd: str, tail: Iterable[str]) -> str:
    """12 hex chars of sha1 over (first command token + normalized error line)."""
    clean_tail = [strip_ansi(line) for line in tail]
    tokens = strip_ansi(cmd).split()
    head = tokens[0] if tokens else ""
    basis = _normalize(head) + "\x00" + _normalize(first_error_line(clean_tail))
    return hashlib.sha1(basis.encode("utf-8", "surrogateescape")).hexdigest()[:12]
