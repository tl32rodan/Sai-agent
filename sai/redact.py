"""Redaction — pure, applied to every byte leaving the machine (PLAN.md §10.1).

Conservative by design (§2): false positives (a long path eaten by the base64
rule) are acceptable; a leaked secret is not.
"""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Values assigned to secret-looking names: KEY= TOKEN= SECRET= PASSWORD= PASS=
# API_KEY= and AWS_*= — matched as a name *containing* those words, so
# GITHUB_TOKEN, MY_API_KEY and AWS_SECRET_ACCESS_KEY are all covered.
_SECRET_NAME = r"(?:[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PASSWD)[A-Za-z0-9_]*|AWS_[A-Za-z_]+)"
# The bare-value branch is \S+ so unbalanced or embedded quotes can never
# leave a partial value behind (over-redaction beats a leak).
_KV_RE = re.compile(
    rf"(?i)\b({_SECRET_NAME})(\s*[=:]\s*)(\"[^\"\n]*\"|'[^'\n]*'|\S+)"
)

_AUTH_RE = re.compile(r"(?i)\b(Authorization\s*:\s*)([^\n]+)")

_SSH_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r".*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",  # unterminated block: redact to end
    re.DOTALL,
)

# Long base64 (incl. url-safe) or hex runs; hex is a subset of this alphabet.
_LONG_RUN_RE = re.compile(r"[A-Za-z0-9+/_-]{33,}={0,3}")


def redact(text: str) -> str:
    text = _SSH_KEY_RE.sub(REDACTED, text)
    text = _KV_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    text = _AUTH_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _LONG_RUN_RE.sub(REDACTED, text)
    return text
