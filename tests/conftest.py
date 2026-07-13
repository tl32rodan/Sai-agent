from __future__ import annotations

import base64

import pytest

from sai.config import Config
from sai.events import CmdEvent

T0 = 1_786_500_000.0


class FakeClock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def config() -> Config:
    return Config()


def make_event(
    t: float = T0, cmd: str = "make lens", cwd: str = "/home/b/x",
    exit: int = 2, dur_s: float = 1.0, pane: str = "%3",
    tail: tuple[str, ...] = (),
) -> CmdEvent:
    return CmdEvent(t=t, cmd=cmd, cwd=cwd, exit=exit, dur_s=dur_s, pane=pane, tail=tail)


def make_tsv(
    t: float = T0, cmd: str = "make lens", cwd: str = "/home/b/x",
    exit: int = 2, dur_s: float = 1.0, pane: str = "%3", kind: str = "cmd",
) -> str:
    b64 = base64.b64encode(cmd.encode()).decode()
    return f"{t}\t{kind}\t{exit}\t{dur_s}\t{cwd}\t{pane}\t{b64}"
