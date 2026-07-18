"""Config: ~/.config/sai/config.toml (PLAN.md Appendix B).

parse_config() is pure; the path helpers and load_config() are the IO edge.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_TOML = """\
[endpoint]
backend = "command"                # "command": spawn an agent CLI · "http": OpenAI-compatible POST
command = "claude -p"              # backend = "command": reads the prompt on stdin
url = "http://jetson.local:8080"   # backend = "http"
model = ""                         # http only; empty: use whatever model the endpoint has loaded
timeout_s = 60

[policy]
cooldown_min = 15
bucket_capacity = 10
bucket_refill_min = 6
ttl_min = 10

[capture]
tail_lines = 40
tail_bytes = 8192
ring_lines = 500

[keys]
pull = "g"    # bound under tmux prefix
"""


@dataclass(frozen=True)
class Config:
    backend: str = "command"  # "command" | "http"
    command: str = "claude -p"
    url: str = "http://jetson.local:8080"
    model: str = ""  # http only; empty: the endpoint's loaded model is used
    timeout_s: float = 60.0
    cooldown_min: float = 15.0
    bucket_capacity: int = 10
    bucket_refill_min: float = 6.0
    ttl_min: float = 10.0
    tail_lines: int = 40
    tail_bytes: int = 8192
    ring_lines: int = 500
    pull_key: str = "g"


def parse_config(toml_text: str) -> Config:
    data = tomllib.loads(toml_text)
    endpoint = data.get("endpoint", {})
    policy = data.get("policy", {})
    capture = data.get("capture", {})
    keys = data.get("keys", {})
    d = Config()
    return Config(
        backend=str(endpoint.get("backend", d.backend)),
        command=str(endpoint.get("command", d.command)),
        url=str(endpoint.get("url", d.url)),
        model=str(endpoint.get("model", d.model)),
        timeout_s=float(endpoint.get("timeout_s", d.timeout_s)),
        cooldown_min=float(policy.get("cooldown_min", d.cooldown_min)),
        bucket_capacity=int(policy.get("bucket_capacity", d.bucket_capacity)),
        bucket_refill_min=float(policy.get("bucket_refill_min", d.bucket_refill_min)),
        ttl_min=float(policy.get("ttl_min", d.ttl_min)),
        tail_lines=int(capture.get("tail_lines", d.tail_lines)),
        tail_bytes=int(capture.get("tail_bytes", d.tail_bytes)),
        ring_lines=int(capture.get("ring_lines", d.ring_lines)),
        pull_key=str(keys.get("pull", d.pull_key)),
    )


def config_path() -> Path:
    if override := os.environ.get("SAI_CONFIG"):
        return Path(override)
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sai" / "config.toml"


def state_dir() -> Path:
    if override := os.environ.get("SAI_STATE_DIR"):
        return Path(override)
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "sai"


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        return parse_config(path.read_text())
    except FileNotFoundError:
        return Config()
