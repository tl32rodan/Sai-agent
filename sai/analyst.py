"""LLM adapter — pull path only (PLAN.md §10). The single place bytes may leave
the machine; everything passes through redact() first (invariant 5)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter
from typing import Sequence

from .redact import redact

# PLAN.md Appendix A, verbatim.
SYSTEM_PROMPT = (
    "You are Sai, a resident terminal advisor. You can see the user's recent "
    "commands and their output, but you cannot execute anything — like a Go "
    "master who may speak but never place a stone. Diagnose the most likely "
    "root cause, teach the underlying concept in two to four sentences so the "
    "user is stronger next time, then suggest one concrete next command. Never "
    "claim to have run anything. Be brief; the user is mid-work."
)

CONTEXT_COMMANDS = 5


class AnalystError(Exception):
    """One-line, popup-safe failure description."""


def gather_context(records: Sequence[dict], n_cmds: int = CONTEXT_COMMANDS) -> str:
    """Format the last n completed commands (+tails, cwd, fingerprint repeat
    counts) from parsed pings.jsonl records. Pure; no redaction here."""
    cmds = [r for r in records if r.get("type") == "cmd"]
    recent = cmds[-n_cmds:]
    if not recent:
        return ""
    fp_counts = Counter(r["fp"] for r in cmds if r.get("fp"))
    lines = [f"cwd: {recent[-1].get('cwd', '?')}", "", "Recent commands, oldest first:"]
    for r in recent:
        note = f"exit {r.get('exit', '?')} · {float(r.get('dur_s', 0)):.1f}s"
        seen = fp_counts.get(r.get("fp"), 0)
        if r.get("exit") != 0 and seen > 1:
            note += f" · this error seen {seen}× recently"
        lines.append(f"$ {r.get('cmd', '?')}   [{note}]")
        if tail := r.get("tail"):
            lines.extend(f"    {t}" for t in tail)
    return "\n".join(lines)


def build_payload(context: str, *, model: str) -> dict:
    """OpenAI-compatible chat payload. The user content is redacted as a whole —
    no byte of gathered context reaches the wire unredacted."""
    user = (
        "Here is my recent terminal activity:\n\n"
        f"{context}\n\n"
        "What is most likely going wrong, why, and what should I try next?"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": redact(user)},
        ],
    }


def ask(url: str, payload: dict, timeout_s: float = 30.0) -> str:
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.load(response)
        return body["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        raise AnalystError(f"endpoint returned HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise AnalystError(f"endpoint unreachable ({e.reason})") from e
    except TimeoutError as e:
        raise AnalystError(f"endpoint timed out after {timeout_s:.0f}s") from e
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise AnalystError("endpoint sent an unexpected response shape") from e
