from __future__ import annotations

import re

from hypothesis import given, strategies as st

from sai.fingerprint import fingerprint, first_error_line


class TestShape:
    def test_twelve_hex_chars(self):
        fp = fingerprint("make lens", ["make: *** [lens] Error 2"])
        assert re.fullmatch(r"[0-9a-f]{12}", fp)

    def test_deterministic(self):
        args = ("python x.py", ["Traceback (most recent call last):"])
        assert fingerprint(*args) == fingerprint(*args)

    def test_empty_everything(self):
        assert re.fullmatch(r"[0-9a-f]{12}", fingerprint("", []))


class TestGoldenInvariance:
    """PLAN.md §7: same error, different volatile details → same fingerprint."""

    def test_paths(self):
        a = fingerprint("cat x", ["cat: /home/alice/project/x.txt: No such file or directory"])
        b = fingerprint("cat x", ["cat: /tmp/build-9f/x.txt: No such file or directory"])
        assert a == b

    def test_pids(self):
        a = fingerprint("./srv", ["worker error, pid 48213 exited"])
        b = fingerprint("./srv", ["worker error, pid 517 exited"])
        assert a == b

    def test_bracketed_jobs_and_pids(self):
        a = fingerprint("./srv", ["[1] 48213 segmentation fault  ./srv"])
        b = fingerprint("./srv", ["[2] 51733 segmentation fault  ./srv"])
        assert a == b

    def test_hex_addresses(self):
        a = fingerprint("./bin", ["Segmentation fault at 0xdeadbeef"])
        b = fingerprint("./bin", ["Segmentation fault at 0x7ffe12aa"])
        assert a == b

    def test_timestamps(self):
        a = fingerprint("job", ["2026-07-12 10:15:03 ERROR db timeout"])
        b = fingerprint("job", ["2026-07-13 23:59:59 ERROR db timeout"])
        assert a == b

    def test_long_integers(self):
        a = fingerprint("curl x", ["error: request 182736 failed"])
        b = fingerprint("curl x", ["error: request 999999 failed"])
        assert a == b

    def test_ansi_stripped(self):
        a = fingerprint("make", ["\x1b[31mError: boom\x1b[0m"])
        b = fingerprint("make", ["Error: boom"])
        assert a == b

    def test_different_errors_differ(self):
        a = fingerprint("make", ["Error: undefined reference to `foo'"])
        b = fingerprint("make", ["Error: Permission denied"])
        assert a != b

    def test_different_commands_differ(self):
        a = fingerprint("make lens", ["Error: boom"])
        b = fingerprint("cargo build", ["Error: boom"])
        assert a != b

    def test_same_command_different_args_group_together(self):
        # only the first token of the command participates (§7)
        a = fingerprint("make lens", ["Error: boom"])
        b = fingerprint("make -j8 lens", ["Error: boom"])
        assert a == b

    @given(st.sampled_from(["/home/a/x", "/tmp/y/z.c", "/var/log/q"]),
           st.integers(min_value=1000, max_value=10**9))
    def test_property_path_and_number_invariance(self, path, number):
        base = fingerprint("cc", ["error: /usr/x.c:9999: failure in build"])
        var = fingerprint("cc", [f"error: {path}:{number}: failure in build"])
        assert base == var


class TestFirstErrorLine:
    def test_picks_first_error_looking_line(self):
        tail = ["compiling...", "warning: unused", "Error: boom", "Error: later"]
        assert first_error_line(tail) == "Error: boom"

    def test_matches_section8_patterns(self):
        for line in (
            "Traceback (most recent call last):",
            "bash: frob: command not found",
            "rm: cannot remove 'x': Permission denied",
            "undefined reference to `main'",
            "Segmentation fault (core dumped)",
            "ls: cannot access 'x': No such file or directory",
        ):
            assert first_error_line(["noise", line]) == line

    def test_falls_back_to_last_nonempty(self):
        assert first_error_line(["alpha", "beta", "", "  "]) == "beta"

    def test_empty_tail(self):
        assert first_error_line([]) == ""
