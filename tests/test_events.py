from __future__ import annotations

from sai.events import (
    BlindEvent, PaneTracker, RingBuffer, blind_to_json, cmd_to_json,
    parse_tsv_line, strip_ansi, with_tail,
)
from tests.conftest import T0, make_tsv


class TestParseTsvLine:
    def test_good_record(self):
        ev = parse_tsv_line(make_tsv(t=T0, cmd="make lens", exit=2, dur_s=94.2))
        assert ev is not None
        assert (ev.t, ev.cmd, ev.exit, ev.dur_s, ev.cwd, ev.pane) == (
            T0, "make lens", 2, 94.2, "/home/b/x", "%3",
        )
        assert ev.start == T0 - 94.2

    def test_unicode_command_roundtrip(self):
        ev = parse_tsv_line(make_tsv(cmd="echo '佐為 — sai'"))
        assert ev is not None and ev.cmd == "echo '佐為 — sai'"

    def test_trailing_newline_tolerated(self):
        assert parse_tsv_line(make_tsv() + "\n") is not None

    def test_wrong_field_count(self):
        assert parse_tsv_line("1.0\tcmd\t0") is None
        assert parse_tsv_line("") is None
        assert parse_tsv_line(make_tsv() + "\textra") is None

    def test_non_cmd_kind(self):
        assert parse_tsv_line(make_tsv(kind="other")) is None

    def test_bad_base64(self):
        line = make_tsv().rsplit("\t", 1)[0] + "\t!!!not-base64!!!"
        assert parse_tsv_line(line) is None

    def test_bad_numbers(self):
        assert parse_tsv_line("abc\tcmd\t0\t1.0\t/x\t%1\tbHM=") is None
        assert parse_tsv_line(f"{T0}\tcmd\tzero\t1.0\t/x\t%1\tbHM=") is None

    def test_negative_duration_clamped(self):
        ev = parse_tsv_line(make_tsv(dur_s=-3.0))
        assert ev is not None and ev.dur_s == 0.0


class TestStripAnsi:
    def test_sgr_colors(self):
        assert strip_ansi("\x1b[31mred\x1b[0m plain") == "red plain"

    def test_cursor_and_erase(self):
        assert strip_ansi("\x1b[2K\x1b[1Gprompt") == "prompt"

    def test_osc_title(self):
        assert strip_ansi("\x1b]0;title\x07text") == "text"

    def test_plain_text_untouched(self):
        assert strip_ansi("make: *** [lens] Error 2") == "make: *** [lens] Error 2"

    def test_keypad_mode_and_charset_escapes(self):
        # observed live: zsh's zle emits ESC= / ESC> around the echoed command
        assert strip_ansi("m\bmake lens\x1b>") == "make lens"
        assert strip_ansi("\x1b=prompt\x1b(B") == "prompt"

    def test_backspaces_applied(self):
        assert strip_ansi("cd /tmp\b\b\bvar") == "cd /var"

    def test_other_control_chars_dropped_tab_kept(self):
        assert strip_ansi("a\x00b\x07c\td") == "abc\td"

    def test_colon_subparameter_sgr(self):
        assert strip_ansi("\x1b[38:5:196mred\x1b[0m") == "red"
        assert strip_ansi("\x1b[4:3mundercurl\x1b[4:0m") == "undercurl"


class TestRingBuffer:
    def test_capacity_evicts_oldest(self):
        ring = RingBuffer(capacity=3)
        for i in range(5):
            ring.append(T0 + i, f"line{i}")
        assert ring.snapshot(T0, T0 + 10) == ("line2", "line3", "line4")

    def test_window_bounds_and_slack(self):
        ring = RingBuffer()
        ring.append(T0 - 1.0, "before")
        ring.append(T0 + 1.0, "inside")
        ring.append(T0 + 5.4, "slack")     # end + 0.5 inclusive
        ring.append(T0 + 6.0, "after")
        assert ring.snapshot(T0, T0 + 5.0) == ("inside", "slack")

    def test_max_lines_keeps_most_recent(self):
        ring = RingBuffer()
        for i in range(10):
            ring.append(T0 + i, f"l{i}")
        assert ring.snapshot(T0, T0 + 20, max_lines=2) == ("l8", "l9")

    def test_max_bytes_keeps_most_recent(self):
        ring = RingBuffer()
        ring.append(T0, "a" * 100)
        ring.append(T0 + 1, "b" * 100)
        snap = ring.snapshot(T0, T0 + 2, max_bytes=150)
        assert snap == ("b" * 100,)

    def test_single_oversized_line_truncated_not_lost(self):
        ring = RingBuffer()
        ring.append(T0, "x" * 9000)
        snap = ring.snapshot(T0, T0 + 1, max_bytes=100)
        assert len(snap) == 1 and snap[0] == "x" * 100

    def test_oversized_line_cap_is_bytes_not_chars(self):
        ring = RingBuffer()
        ring.append(T0, "佐" * 9000)  # 3 bytes per char in UTF-8
        (line,) = ring.snapshot(T0, T0 + 1, max_bytes=100)
        assert len(line.encode()) <= 100
        assert line == "佐" * 33  # truncation never splits a character

    def test_ansi_stripped_in_snapshot(self):
        ring = RingBuffer()
        ring.append(T0, "\x1b[31mError\x1b[0m: boom")
        assert ring.snapshot(T0, T0 + 1) == ("Error: boom",)


class TestPaneTracker:
    def test_complete_lines_reach_ring(self):
        tr = PaneTracker()
        assert tr.feed(T0, "one\ntwo\n") == []
        assert tr.ring.snapshot(T0, T0 + 1) == ("one", "two")

    def test_partial_line_assembled_across_feeds(self):
        tr = PaneTracker()
        tr.feed(T0, "hel")
        tr.feed(T0 + 0.1, "lo\n")
        assert tr.ring.snapshot(T0, T0 + 1) == ("hello",)

    def test_crlf_normalized(self):
        tr = PaneTracker()
        tr.feed(T0, "a\r\nb\r")
        tr.feed(T0, "c\n")
        # lone \r is a line break too (progress bars)
        assert tr.ring.snapshot(T0, T0 + 1) == ("a", "b", "c")

    def test_crlf_split_across_chunks_no_phantom_empty_line(self):
        tr = PaneTracker()
        tr.feed(T0, "a\r")
        tr.feed(T0 + 0.1, "\nb\n")
        assert tr.ring.snapshot(T0, T0 + 1) == ("a", "b")

    def test_blind_enter_exit_transitions(self):
        tr = PaneTracker()
        assert tr.feed(T0, "before\n\x1b[?1049h") == ["enter"]
        assert tr.blind is True
        assert tr.feed(T0 + 1, "\x1b[?1049l") == ["exit"]
        assert tr.blind is False

    def test_legacy_toggle_variants(self):
        for seq in ("\x1b[?47h", "\x1b[?1047h"):
            tr = PaneTracker()
            assert tr.feed(T0, seq) == ["enter"]

    def test_repeated_enter_not_duplicated(self):
        tr = PaneTracker()
        assert tr.feed(T0, "\x1b[?1049h\x1b[?1049h") == ["enter"]
        assert tr.feed(T0, "\x1b[?1049l\x1b[?1049l") == ["exit"]

    def test_blind_content_excluded_from_ring(self):
        tr = PaneTracker()
        tr.feed(T0, "visible\n\x1b[?1049hTUI noise\nmore noise\n\x1b[?1049lafter\n")
        assert tr.ring.snapshot(T0, T0 + 1) == ("visible", "after")

    def test_toggle_split_across_chunks_still_detected(self):
        tr = PaneTracker()
        assert tr.feed(T0, "text\x1b[?104") == []
        assert tr.feed(T0 + 0.2, "9h") == ["enter"]
        assert tr.blind is True

    def test_partial_line_cut_by_blind_entry_dropped(self):
        tr = PaneTracker()
        tr.feed(T0, "half-a-line\x1b[?1049h")
        tr.feed(T0, "\x1b[?1049l")
        tr.feed(T0, "whole\n")
        assert tr.ring.snapshot(T0, T0 + 1) == ("whole",)


class TestCanonicalJson:
    def test_cmd_to_json_matches_plan_schema(self):
        ev = parse_tsv_line(make_tsv(t=T0, cmd="make lens", exit=2, dur_s=94.2))
        ev = with_tail(ev, ["make: error"])
        record = cmd_to_json(ev, "abc123def456")
        assert record == {
            "t": T0, "type": "cmd", "cmd": "make lens", "cwd": "/home/b/x",
            "exit": 2, "dur_s": 94.2, "pane": "%3",
            "tail": ["make: error"], "fp": "abc123def456",
        }

    def test_blind_to_json(self):
        assert blind_to_json(BlindEvent(T0, "%3", "enter")) == {
            "t": T0, "type": "blind", "pane": "%3", "state": "enter",
        }
