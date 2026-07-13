from __future__ import annotations

import json

import pytest

from sai.cli import main, parse_tmux_version
from tests.conftest import T0


class TestParseTmuxVersion:
    @pytest.mark.parametrize("output,expected", [
        ("tmux 3.4", (3, 4)),
        ("tmux 3.3a", (3, 3)),
        ("tmux 3.2a", (3, 2)),
        ("tmux next-3.6", (3, 6)),
        ("tmux 2.9", (2, 9)),
        ("garbage", None),
        ("", None),
    ])
    def test_parses(self, output, expected):
        assert parse_tmux_version(output) == expected

    def test_minimum_check(self):
        assert parse_tmux_version("tmux 3.1c") < (3, 2)
        assert parse_tmux_version("tmux 3.2a") >= (3, 2)


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("SAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SAI_CONFIG", str(tmp_path / "config.toml"))
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    return tmp_path / "state"


class TestStatsCommand:
    def test_empty_state(self, state, capsys):
        assert main(["stats"]) == 0
        out = capsys.readouterr().out
        assert "sai stats" in out and "no data yet" in out

    def test_days_flag(self, state, capsys):
        assert main(["stats", "--days", "7"]) == 0
        assert "last 7 day(s)" in capsys.readouterr().out


class TestLogCommand:
    def test_no_log_yet(self, state, capsys):
        assert main(["log"]) == 0
        assert "no audit log yet" in capsys.readouterr().out

    def test_prints_recent_records(self, state, capsys):
        state.mkdir(parents=True)
        with (state / "pings.jsonl").open("w") as f:
            for i in range(30):
                f.write(json.dumps({"t": T0 + i, "type": "pull"}) + "\n")
        assert main(["log", "-n", "5"]) == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 5
        assert json.loads(lines[-1])["t"] == T0 + 29


class TestPullCommand:
    def test_nothing_observed_yet(self, state, capsys):
        assert main(["pull"]) == 0
        assert "nothing observed yet" in capsys.readouterr().out
        records = [
            json.loads(line)
            for line in (state / "pings.jsonl").read_text().splitlines()
        ]
        assert [r["type"] for r in records] == ["pull"]  # pulls are audited too

    def test_endpoint_failure_is_graceful_one_liner(self, state, tmp_path, capsys):
        (tmp_path / "config.toml").write_text(
            '[endpoint]\nurl = "http://127.0.0.1:9"\ntimeout_s = 2\n'
        )
        state.mkdir(parents=True)
        (state / "pings.jsonl").write_text(json.dumps({
            "t": T0, "type": "cmd", "cmd": "make", "cwd": "/x", "exit": 2,
            "dur_s": 1.0, "pane": "%1", "tail": ["boom"], "fp": "a" * 12,
        }) + "\n")
        assert main(["pull"]) == 0  # never a stack trace in the popup (§10)
        out = capsys.readouterr().out
        assert out.startswith("sai ▸ analyst unavailable:")
        assert "Traceback" not in out


class TestVersionFlag:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.startswith("sai ")
