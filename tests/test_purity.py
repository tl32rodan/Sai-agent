"""PLAN.md §13 definition of done: the pure core must contain no IO and no
clock — enforced by grep, exactly as prescribed."""
from __future__ import annotations

from pathlib import Path

PURE_MODULES = [
    "events.py", "fingerprint.py", "salience.py", "policy.py", "redact.py",
    "status.py",
]

FORBIDDEN_TOKENS = [
    "time.time", "import time", "datetime",           # no clock
    "open(", "pathlib", "Path(", "tempfile",          # no filesystem
    "subprocess",                                      # no processes
    "urllib", "socket", "http",                        # no network
    "import os", "os.environ", "sys.std", "input(",   # no environment/tty
    "random",                                          # no nondeterminism
]


def test_pure_core_has_no_io_and_no_clock():
    package_dir = Path(__file__).resolve().parent.parent / "sai"
    for module in PURE_MODULES:
        source = (package_dir / module).read_text()
        for token in FORBIDDEN_TOKENS:
            assert token not in source, f"sai/{module} contains forbidden {token!r}"


def test_pure_core_modules_all_exist():
    package_dir = Path(__file__).resolve().parent.parent / "sai"
    for module in PURE_MODULES:
        assert (package_dir / module).is_file()
