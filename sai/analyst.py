"""LLM adapter — pull path only (PLAN.md §10, §16.3). The single place bytes
may leave the machine; everything passes through redact() first (invariant 5).

Two backends: "command" spawns an agent CLI (default `claude -p` — reuse a
tuned harness instead of rebuilding one; Sai is a layer above it) and "http"
POSTs to an OpenAI-compatible endpoint (e.g. llama.cpp, fully local)."""
from __future__ import annotations

import http.client
import json
import shlex
import subprocess
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


def _user_text(context: str) -> str:
    return (
        "Here is my recent terminal activity:\n\n"
        f"{context}\n\n"
        "What is most likely going wrong, why, and what should I try next?"
    )


def build_payload(context: str, *, model: str) -> dict:
    """OpenAI-compatible chat payload (http backend). The user content is
    redacted as a whole — no byte of gathered context leaves unredacted."""
    payload: dict = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": redact(_user_text(context))},
        ],
    }
    if model:  # empty model: omit the field — the endpoint's loaded model is used
        payload["model"] = model
    return payload


def build_prompt(context: str) -> str:
    """Single-text prompt (command backend). Redacted before it reaches the
    spawned agent — same invariant-5 boundary as the http payload."""
    return SYSTEM_PROMPT + "\n\n" + redact(_user_text(context))


# The endpoint the user configured is the only place this data may go: an
# opener with no proxies keeps generic http_proxy/https_proxy environment
# variables from silently rerouting the context through another host.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ask(url: str, payload: dict, timeout_s: float = 30.0) -> str:
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener.open(request, timeout=timeout_s) as response:
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
    except (OSError, http.client.HTTPException) as e:
        # dropped connection mid-body, truncated reads, TLS errors, ...
        raise AnalystError(f"connection failed ({e.__class__.__name__})") from e


def ask_command(
    command: str, prompt: str, timeout_s: float = 60.0, cwd: str | None = None,
) -> str:
    """Spawn an agent CLI (e.g. `claude -p`) with the redacted prompt on stdin.
    cwd is the user's most recent working directory so the agent can read the
    project it is being asked about."""
    argv = shlex.split(command)
    if not argv:
        raise AnalystError("analyst command is empty — set [endpoint] command")
    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True,
            timeout=timeout_s, cwd=cwd,
        )
    except FileNotFoundError as e:
        raise AnalystError(f"command not found: {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise AnalystError(f"{argv[0]} timed out after {timeout_s:.0f}s") from e
    except OSError as e:
        raise AnalystError(f"could not run {argv[0]} ({e.__class__.__name__})") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = f": {tail[-1][:60]}" if tail else ""
        raise AnalystError(f"{argv[0]} exited {proc.returncode}{detail}")
    reply = proc.stdout.strip()
    if not reply:
        raise AnalystError(f"{argv[0]} returned no output")
    return reply
