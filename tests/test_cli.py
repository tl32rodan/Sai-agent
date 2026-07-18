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
            '[endpoint]\nbackend = "http"\nurl = "http://127.0.0.1:9"\ntimeout_s = 2\n'
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

    def test_command_backend_failure_is_graceful_one_liner(self, state, tmp_path, capsys):
        (tmp_path / "config.toml").write_text(
            '[endpoint]\nbackend = "command"\ncommand = "definitely-missing-binary-xyz"\n'
        )
        state.mkdir(parents=True)
        (state / "pings.jsonl").write_text(json.dumps({
            "t": T0, "type": "cmd", "cmd": "make", "cwd": "/x", "exit": 2,
            "dur_s": 1.0, "pane": "%1", "tail": ["boom"], "fp": "a" * 12,
        }) + "\n")
        assert main(["pull"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("sai ▸ analyst unavailable:")
        assert "command not found" in out

    def test_command_backend_happy_path_via_cat(self, state, tmp_path, capsys):
        # `cat` as the agent CLI: the reply is the redacted prompt itself
        (tmp_path / "config.toml").write_text(
            '[endpoint]\nbackend = "command"\ncommand = "cat"\n'
        )
        state.mkdir(parents=True)
        (state / "pings.jsonl").write_text(json.dumps({
            "t": T0, "type": "cmd", "cmd": "make lens", "cwd": "/x", "exit": 2,
            "dur_s": 1.0, "pane": "%1", "tail": ["Error: boom"], "fp": "a" * 12,
        }) + "\n")
        assert main(["pull"]) == 0
        out = capsys.readouterr().out
        assert "You are Sai" in out and "$ make lens" in out


class TestStatusCommand:
    def test_no_status_yet(self, state, capsys):
        assert main(["status"]) == 0
        assert "no status yet" in capsys.readouterr().out

    def test_prints_status_file(self, state, capsys):
        state.mkdir(parents=True)
        (state / "status").write_text("⏺ make ok · just now\n▷ all quiet · C-b g anytime\n")
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("⏺ make ok") and "▷ all quiet" in out


class TestInstallBlock:
    def test_creates_and_rewrites_idempotently(self, tmp_path):
        from sai.cli import _install_block
        rc = tmp_path / "rc"
        assert _install_block(rc, "source /repo/sai.zsh") is True
        assert _install_block(rc, "source /repo/sai.zsh") is False  # unchanged
        assert _install_block(rc, "source /elsewhere/sai.zsh") is True
        content = rc.read_text()
        assert "/elsewhere/" in content and "/repo/" not in content

    def test_orphaned_begin_marker_does_not_swallow_user_content(self, tmp_path):
        from sai.cli import _MARK_BEGIN, _install_block
        rc = tmp_path / "rc"
        rc.write_text(f"{_MARK_BEGIN}\nalias ll='ls -l'\n")  # corrupted: no end marker
        _install_block(rc, "source /repo/sai.zsh")
        _install_block(rc, "source /repo/sai.zsh")  # the dangerous second pass
        content = rc.read_text()
        assert "alias ll='ls -l'" in content
        assert content.count("source /repo/sai.zsh") == 1


class TestVersionFlag:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.startswith("sai ")
