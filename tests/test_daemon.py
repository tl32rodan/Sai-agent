"""M0.a acceptance: replaying a scripted event sequence through the daemon core
produces exactly the expected verdict sequence (golden test), plus one smoke
test for the file-tailing IO shell."""
from __future__ import annotations

import json

from sai.config import Config
from sai.daemon import Daemon, DaemonCore
from tests.conftest import FakeClock, make_tsv


def make_core(clock):
    pushes: list[tuple[str, str]] = []
    log: list[dict] = []
    core = DaemonCore(
        Config(), clock=clock,
        push=lambda pane, text: pushes.append((pane, text)), log=log.append,
    )
    return core, pushes, log


def test_golden_replay():
    clock = FakeClock()
    core, pushes, log = make_core(clock)

    # 1. healthy command → silence
    core.on_pane_output("%1", "src  README.md\n")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="ls", exit=0, dur_s=0.1, pane="%1"))

    # 2. induced make failure → exactly one ping (M0.a acceptance)
    clock.advance(10)
    core.on_pane_output("%1", "make: *** [lens] Error 2\n")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make lens", exit=2, dur_s=1.0, pane="%1"))

    # 3. identical failure 1 min later → none (cooldown; M0.a acceptance)
    clock.advance(60)
    core.on_pane_output("%1", "make: *** [lens] Error 2\n")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make lens", exit=2, dur_s=1.0, pane="%1"))

    # 4. vim opens (alternate screen); a command completing inside is suppressed
    clock.advance(30)
    core.on_pane_output("%1", "\x1b[?1049h")
    clock.advance(5)
    core.on_tsv_line(make_tsv(t=clock.t, cmd="git status", exit=1, dur_s=0.1, pane="%1"))
    clock.advance(5)
    core.on_pane_output("%1", "\x1b[?1049l")

    verdicts = [
        (r["verdict"], r.get("rule"), r.get("reason"))
        for r in log if r["type"] == "verdict"
    ]
    assert verdicts == [
        ("push", "r1", None),          # the failure speaks once…
        ("drop", "r2", "cooldown"),    # …the repeat is a struggle but stays silent
        ("drop", None, "blind"),       # …and blind spans never speak at all
    ]

    assert len(pushes) == 1
    pane, text = pushes[0]
    assert pane == "%1"
    assert text.startswith("sai ▸ exit 2: make")
    assert len(text) <= 80

    assert sum(1 for r in log if r["type"] == "cmd") == 4
    assert [(r["state"]) for r in log if r["type"] == "blind"] == ["enter", "exit"]


def test_tail_snapshot_attached_to_cmd_event():
    clock = FakeClock()
    core, _pushes, log = make_core(clock)
    core.on_pane_output("%2", "old noise\n")
    clock.advance(300)
    core.on_pane_output("%2", "cc -c lens.c\nlens.c:9: undefined reference to `f'\n")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make", exit=2, dur_s=2.0, pane="%2"))
    (cmd_record,) = [r for r in log if r["type"] == "cmd"]
    assert cmd_record["tail"] == ["cc -c lens.c", "lens.c:9: undefined reference to `f'"]
    assert "old noise" not in cmd_record["tail"]


def test_blind_span_failures_never_feed_struggle_history():
    # §6: blind spans are excluded from salience entirely — including as the
    # history that R2 compares later commands against.
    clock = FakeClock()
    core, pushes, log = make_core(clock)
    core.on_pane_output("%1", "\x1b[?1049h")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make lens", exit=2, pane="%1"))
    clock.advance(30)
    core.on_pane_output("%1", "\x1b[?1049l")
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make lens", exit=2, pane="%1"))
    visible = [r for r in log if r["type"] == "verdict" and r.get("reason") != "blind"]
    assert [(r["verdict"], r["rule"]) for r in visible] == [("push", "r1")]


def test_events_for_unknown_pane_still_processed():
    clock = FakeClock()
    core, pushes, log = make_core(clock)
    core.on_tsv_line(make_tsv(t=clock.t, cmd="false", exit=1, dur_s=0.1, pane="none"))
    assert [r["type"] for r in log] == ["cmd", "verdict"]
    assert len(pushes) == 1  # no pane log yet, but the event still counts


def test_malformed_tsv_ignored():
    clock = FakeClock()
    core, pushes, log = make_core(clock)
    core.on_tsv_line("garbage\tline")
    core.on_tsv_line("")
    assert log == [] and pushes == []


class TestDaemonIOSmoke:
    def test_end_to_end_through_files(self, tmp_path):
        clock = FakeClock()
        pushes: list[tuple[str, str]] = []
        daemon = Daemon(
            tmp_path, Config(), clock=clock,
            push=lambda pane, text: pushes.append((pane, text)),
        )
        (tmp_path / "out-%1.log").write_bytes(b"make: *** [lens] Error 2\n")
        (tmp_path / "events.tsv").write_text(
            make_tsv(t=clock.t, cmd="make lens", exit=2, dur_s=1.0, pane="%1") + "\n"
        )
        daemon.step()
        assert len(pushes) == 1 and pushes[0][0] == "%1"
        records = [
            json.loads(line)
            for line in (tmp_path / "pings.jsonl").read_text().splitlines()
        ]
        assert [r["type"] for r in records] == ["cmd", "verdict"]
        assert records[0]["tail"] == ["make: *** [lens] Error 2"]

    def test_partial_tsv_line_waits_for_completion(self, tmp_path):
        clock = FakeClock()
        pushes: list[tuple[str, str]] = []
        daemon = Daemon(tmp_path, Config(), clock=clock,
                        push=lambda pane, text: pushes.append((pane, text)))
        line = make_tsv(t=clock.t, cmd="false", exit=1, dur_s=0.1, pane="%1")
        (tmp_path / "events.tsv").write_text(line[:10])
        daemon.step()
        assert pushes == []
        with (tmp_path / "events.tsv").open("a") as f:
            f.write(line[10:] + "\n")
        daemon.step()
        assert len(pushes) == 1

    def test_restart_does_not_replay_history(self, tmp_path):
        clock = FakeClock()
        first_pushes: list[tuple[str, str]] = []
        daemon = Daemon(tmp_path, Config(), clock=clock,
                        push=lambda pane, text: first_pushes.append((pane, text)))
        (tmp_path / "out-%1.log").write_bytes(b"make: *** [lens] Error 2\n")
        (tmp_path / "events.tsv").write_text(
            make_tsv(t=clock.t, cmd="make lens", exit=2, pane="%1") + "\n")
        daemon.step()
        assert len(first_pushes) == 1
        log_size = (tmp_path / "pings.jsonl").stat().st_size

        # restart 30 s later: history must not be replayed — no duplicate
        # audit records, no re-push inside the cooldown window
        clock.advance(30)
        second_pushes: list[tuple[str, str]] = []
        restarted = Daemon(tmp_path, Config(), clock=clock,
                           push=lambda pane, text: second_pushes.append((pane, text)))
        restarted.step()
        assert second_pushes == []
        assert (tmp_path / "pings.jsonl").stat().st_size == log_size

        # …but events appended after the restart are picked up
        with (tmp_path / "events.tsv").open("a") as f:
            f.write(make_tsv(t=clock.t, cmd="cargo build", exit=1, pane="%1") + "\n")
        restarted.step()
        assert len(second_pushes) == 1

    def test_utf8_char_split_across_polls(self, tmp_path):
        clock = FakeClock()
        daemon = Daemon(tmp_path, Config(), clock=clock, push=lambda p, t: None)
        raw = "error: 佐為 not found\n".encode()
        (tmp_path / "out-%1.log").write_bytes(raw[:9])  # cuts 佐 mid-sequence
        daemon.step()
        with (tmp_path / "out-%1.log").open("ab") as f:
            f.write(raw[9:])
        daemon.step()
        (tmp_path / "events.tsv").write_text(
            make_tsv(t=clock.t, cmd="make", exit=2, pane="%1") + "\n")
        daemon.step()
        records = [
            json.loads(line)
            for line in (tmp_path / "pings.jsonl").read_text().splitlines()
        ]
        (cmd_record,) = [r for r in records if r["type"] == "cmd"]
        assert cmd_record["tail"] == ["error: 佐為 not found"]

    def test_status_file_written_and_updated(self, tmp_path):
        clock = FakeClock()
        daemon = Daemon(tmp_path, Config(), clock=clock, push=lambda p, t: None)
        daemon.step()
        status = (tmp_path / "status").read_text()
        assert "watching" in status  # nothing observed yet
        (tmp_path / "out-%1.log").write_bytes(b"make: *** [lens] Error 2\n")
        (tmp_path / "events.tsv").write_text(
            make_tsv(t=clock.t, cmd="make lens", exit=2, pane="%1") + "\n")
        daemon.step()
        status = (tmp_path / "status").read_text()
        line1, line2, _ = status.split("\n")
        assert "make" in line1 and "exit 2" in line1
        assert "C-b g" in line2

    def test_truncated_file_resets_offset(self, tmp_path):
        clock = FakeClock()
        pushes: list[tuple[str, str]] = []
        daemon = Daemon(tmp_path, Config(), clock=clock,
                        push=lambda pane, text: pushes.append((pane, text)))
        tsv = tmp_path / "events.tsv"
        tsv.write_text(make_tsv(t=clock.t, cmd="false", exit=1, pane="%1") + "\n")
        daemon.step()
        clock.advance(2000)
        # rewrite with a strictly smaller file so the tailer sees the truncation
        tsv.write_text(make_tsv(t=clock.t, cmd="ok", cwd="/x", exit=0, pane="%1") + "\n")
        daemon.step()
        records = [
            json.loads(line)
            for line in (tmp_path / "pings.jsonl").read_text().splitlines()
        ]
        cmds = [r["cmd"] for r in records if r["type"] == "cmd"]
        assert cmds == ["false", "ok"]
