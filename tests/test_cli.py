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
    monkeypatch.setenv("SAI_RUNTIME_DIR", str(tmp_path / "run"))
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

    def test_prints_status_file_from_runtime_dir(self, state, tmp_path, capsys):
        run = tmp_path / "run"
        run.mkdir(parents=True)
        (run / "status").write_text("⏺ make ok · just now\n▷ all quiet · C-b g anytime\n")
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


class TestPidfile:
    def test_claim_then_conflict_then_stale_recovery(self, tmp_path):
        import subprocess

        from sai.cli import _claim_pidfile, _live_pid
        pidfile = tmp_path / "daemon.pid"
        assert _claim_pidfile(pidfile) is True  # our own (live) pid is written
        assert _claim_pidfile(pidfile) is False  # second daemon must back off
        # a crashed daemon leaves a dead pid behind: the claim self-heals
        dead = subprocess.Popen(["true"])
        dead.wait()
        pidfile.write_text(str(dead.pid))
        assert _live_pid(pidfile) is None
        assert _claim_pidfile(pidfile) is True

    def test_garbage_pidfile_is_treated_as_stale(self, tmp_path):
        from sai.cli import _claim_pidfile
        pidfile = tmp_path / "daemon.pid"
        pidfile.write_text("not-a-pid")
        assert _claim_pidfile(pidfile) is True


class TestEnsureDaemon:
    def test_noop_when_daemon_alive(self, state, tmp_path, monkeypatch):
        import os
        import subprocess as sp
        (tmp_path / "run").mkdir(parents=True, exist_ok=True)
        (tmp_path / "run" / "daemon.pid").write_text(str(os.getpid()))
        spawned = []
        monkeypatch.setattr(sp, "Popen", lambda *a, **k: spawned.append(a))
        assert main(["ensure-daemon"]) == 0
        assert spawned == []

    def test_spawns_when_absent(self, state, monkeypatch):
        import subprocess as sp

        class FakeProc:
            pid = 12345

        spawned = []

        def fake_popen(argv, **kwargs):
            spawned.append((argv, kwargs))
            return FakeProc()

        monkeypatch.setattr(sp, "Popen", fake_popen)
        assert main(["ensure-daemon"]) == 0
        (call,) = spawned
        assert "from sai.cli import main" in call[0][2]
        assert call[1]["start_new_session"] is True


class TestAllHostsStats:
    def test_aggregates_across_host_dirs(self, state, tmp_path, capsys):
        # state root is the parent of SAI_STATE_DIR: build sibling host dirs
        root = (tmp_path / "state").parent
        for host, t in (("hosta", T0), ("hostb", T0 + 10)):
            d = root / host
            d.mkdir(parents=True, exist_ok=True)
            (d / "pings.jsonl").write_text(json.dumps({"t": t, "type": "pull"}) + "\n")
        assert main(["stats", "--all-hosts"]) == 0
        out = capsys.readouterr().out
        assert "across 2 host(s)" in out
        assert "pulls:  2" in out


class TestVersionFlag:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.startswith("sai ")
