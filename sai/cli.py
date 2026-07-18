"""sai init | daemon | pull | stats | log (PLAN.md §12)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .analyst import (
    AnalystError, ask, ask_command, build_payload, build_prompt, gather_context,
)
from .config import (
    DEFAULT_CONFIG_TOML, config_path, load_config, runtime_dir, short_hostname,
    state_dir, state_root,
)
from .daemon import Daemon
from .stats import compute_stats, render_stats

MIN_TMUX = (3, 2)
_MARK_BEGIN = "# >>> sai >>>"
_MARK_END = "# <<< sai <<<"

_repo_root = Path(__file__).resolve().parent.parent


def parse_tmux_version(version_output: str) -> tuple[int, int] | None:
    """'tmux 3.4' / 'tmux 3.3a' / 'tmux next-3.6' → (major, minor)."""
    m = re.search(r"(\d+)\.(\d+)", version_output)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_pings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _read_pings(limit: int | None = None) -> list[dict]:
    records = _parse_pings(state_dir() / "pings.jsonl")
    return records[-limit:] if limit else records


def _read_pings_all_hosts() -> tuple[list[dict], int]:
    """Merge every host's audit log under the shared state root (§16.5)."""
    records: list[dict] = []
    hosts = 0
    for path in sorted(state_root().glob("*/pings.jsonl")):
        hosts += 1
        records.extend(_parse_pings(path))
    records.sort(key=lambda r: r.get("t", 0))
    return records, hosts


def _ensure_runtime_dir() -> Path:
    rd = runtime_dir()
    rd.mkdir(parents=True, exist_ok=True)
    try:
        rd.chmod(0o700)  # pane context is private, even in /tmp fallback
    except OSError:
        pass
    return rd


def _live_pid(pidfile: Path) -> int | None:
    try:
        pid = int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid  # alive, someone else's — treat as running
    return pid


def _claim_pidfile(pidfile: Path) -> bool:
    """Single daemon per host: atomic O_EXCL claim on the host-local runtime
    dir (never NFS — remote lock semantics are exactly what we avoid)."""
    for _ in range(2):
        try:
            fd = os.open(pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _live_pid(pidfile) is not None:
                return False
            pidfile.unlink(missing_ok=True)  # stale: crashed without cleanup
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def _append_ping(record: dict) -> None:
    path = state_dir() / "pings.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _install_block(path: Path, block: str) -> bool:
    """Idempotently (re)write the marker-delimited block in `path`."""
    content = path.read_text() if path.exists() else ""
    wrapped = f"{_MARK_BEGIN}\n{block}\n{_MARK_END}\n"
    # Tempered dot: a block never spans another begin marker, so an orphaned
    # begin marker earlier in the file can't swallow user content up to our
    # real block's end marker.
    begin, end = re.escape(_MARK_BEGIN), re.escape(_MARK_END)
    pattern = re.compile(rf"{begin}(?:(?!{begin}).)*?{end}\n?", re.DOTALL)
    if pattern.search(content):
        updated = pattern.sub(wrapped, content)
    else:
        updated = content + ("" if content.endswith("\n") or not content else "\n") + wrapped
    if updated != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated)
        return True
    return False


def cmd_init(_args: argparse.Namespace) -> int:
    # 1. Fail loudly on missing prerequisites (PLAN.md §10, §12).
    if not shutil.which("zsh"):
        print("sai init: zsh not found — Sai's sensor is zsh preexec/precmd hooks.\n"
              "Install zsh and make it your login shell, then re-run `sai init`.")
        return 1
    if not shutil.which("tmux"):
        print("sai init: tmux not found — Sai needs tmux ≥ 3.2 for pipe-pane and "
              "display-popup.\nInstall tmux, then re-run `sai init`.")
        return 1
    out = subprocess.run(["tmux", "-V"], capture_output=True, text=True, check=False)
    version = parse_tmux_version(out.stdout or out.stderr)
    if version is None or version < MIN_TMUX:
        found = (out.stdout or out.stderr).strip() or "unknown"
        print(f"sai init: tmux ≥ {MIN_TMUX[0]}.{MIN_TMUX[1]} required for display-popup; "
              f"found: {found}.\nUpgrade tmux (https://github.com/tmux/tmux) and re-run "
              "`sai init`.")
        return 1

    shell_snippet = _repo_root / "shell" / "sai.zsh"
    tmux_conf = _repo_root / "tmux" / "sai.conf"
    if not shell_snippet.exists() or not tmux_conf.exists():
        print(f"sai init: expected repo assets at {shell_snippet} and {tmux_conf}.\n"
              "Run sai from a git clone of the repository (see README install).")
        return 1

    # 2. State + default config.
    sdir = state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    default_sdir = Path.home() / ".local" / "state" / "sai" / short_hostname()
    if sdir != default_sdir:
        print(f"WARNING: state dir is {sdir}, but tmux/sai.conf pipes new panes to\n"
              f"         {default_sdir} — edit the paths in {tmux_conf} to match.")
    cpath = config_path()
    if not cpath.exists():
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(DEFAULT_CONFIG_TOML)
        print(f"wrote default config → {cpath}  (edit the [endpoint] section)")

    # 3. Idempotent snippet installs.
    zshrc = Path.home() / ".zshrc"
    if _install_block(zshrc, f"source {shell_snippet}"):
        print(f"installed zsh hooks → {zshrc}")
    tmux_rc = Path.home() / ".tmux.conf"
    if _install_block(tmux_rc, f"source-file {tmux_conf}"):
        print(f"installed tmux include → {tmux_rc}")

    # 4. `sai` on PATH (repo-local wrapper; no pip needed — runtime is stdlib-only).
    #    A Python script with the path embedded via repr avoids shell quoting
    #    entirely, whatever characters the repo path contains.
    if not shutil.which("sai"):
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / "sai"
        wrapper.write_text(
            "#!/usr/bin/env python3\n# managed by sai init\n"
            "import sys\n"
            f"sys.path.insert(0, {str(_repo_root)!r})\n"
            "from sai.cli import main\n"
            "sys.exit(main())\n"
        )
        wrapper.chmod(0o755)
        print(f"installed wrapper → {wrapper}  (ensure ~/.local/bin is on PATH)")

    # 5. Enable pipe-pane on panes that already exist (new ones are covered by
    #    the set-hooks in tmux/sai.conf).
    panes = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
        capture_output=True, text=True, check=False,
    )
    if panes.returncode == 0:
        for pane in panes.stdout.split():
            subprocess.run(
                ["tmux", "pipe-pane", "-O", "-t", pane,
                 f"mkdir -p '{sdir}' && exec cat >> '{sdir}/out-{pane}.log'"],
                check=False,
            )
        print(f"pipe-pane enabled on {len(panes.stdout.split())} existing pane(s)")
    else:
        print("no running tmux server — new panes will be piped via tmux hooks")

    print("sai init: done. Restart zsh and `tmux source-file ~/.tmux.conf`, then run "
          "`sai daemon` inside tmux.")
    return 0


def cmd_daemon(_args: argparse.Namespace) -> int:
    rd = _ensure_runtime_dir()
    pidfile = rd / "daemon.pid"
    if not _claim_pidfile(pidfile):
        print(f"sai daemon already running on {short_hostname()} (pid {_live_pid(pidfile)})")
        return 0
    config = load_config()
    daemon = Daemon(state_dir(), config, runtime_dir=rd)
    print(f"sai daemon v{__version__} — watching {state_dir()} (Ctrl-C to stop)")
    try:
        daemon.run()
    except KeyboardInterrupt:
        pass
    finally:
        pidfile.unlink(missing_ok=True)
    return 0


def cmd_ensure_daemon(_args: argparse.Namespace) -> int:
    """Idempotent per-host start — called from the zsh snippet so ssh-ing
    into any host of an NFS-homed fleet brings its daemon up lazily (§16.5)."""
    rd = _ensure_runtime_dir()
    if _live_pid(rd / "daemon.pid") is not None:
        return 0
    sdir = state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    with (sdir / "daemon.log").open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(_repo_root)!r}); "
             "from sai.cli import main; sys.exit(main(['daemon']))"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True,
        )
    return 0


def cmd_pull(_args: argparse.Namespace) -> int:
    config = load_config()
    now = time.time()
    records = _read_pings(limit=2000)
    context = gather_context(records)
    _append_ping({"t": now, "type": "pull"})
    if not context:
        print("sai ▸ nothing observed yet — run a few commands first")
        return 0
    try:
        if config.backend == "command":
            # run in the user's latest cwd so the agent can read the project
            cwd = next(
                (r["cwd"] for r in reversed(records)
                 if r.get("type") == "cmd" and r.get("cwd")), None)
            if cwd is not None and not os.path.isdir(cwd):
                cwd = None
            reply = ask_command(config.command, build_prompt(context),
                                timeout_s=config.timeout_s, cwd=cwd)
        elif config.backend == "http":
            model = os.environ.get("SAI_MODEL", config.model)
            reply = ask(config.url, build_payload(context, model=model),
                        timeout_s=config.timeout_s)
        else:
            raise AnalystError(
                f"unknown backend {config.backend!r} — use \"command\" or \"http\"")
    except AnalystError as e:
        print(f"sai ▸ analyst unavailable: {e}")
        return 0  # graceful one-liner; never a stack trace in the popup (§10)
    print(reply)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    path = runtime_dir() / "status"
    if path.exists():
        print(path.read_text(), end="")
    else:
        print("sai ▸ no status yet — is `sai daemon` running on this host?")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    if args.all_hosts:
        records, hosts = _read_pings_all_hosts()
    else:
        records, hosts = _read_pings(), None
    stats = compute_stats(records, now=time.time(), days=args.days)
    print(render_stats(stats, days=args.days, hosts=hosts))
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    path = state_dir() / "pings.jsonl"
    if not path.exists():
        print(f"no audit log yet at {path}")
        return 0
    for record in _read_pings(limit=args.n):
        print(json.dumps(record, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sai",
        description="Sai (佐為) — ambient, observe-only terminal advisor",
    )
    parser.add_argument("--version", action="version", version=f"sai {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="install hooks + config, verify tmux/zsh").set_defaults(fn=cmd_init)
    sub.add_parser("daemon", help="run the observer daemon").set_defaults(fn=cmd_daemon)
    sub.add_parser(
        "ensure-daemon", help="start the daemon for this host if not already running",
    ).set_defaults(fn=cmd_ensure_daemon)
    sub.add_parser("pull", help="ask the analyst about recent activity").set_defaults(fn=cmd_pull)
    sub.add_parser("status", help="two-line ambient status (for panes/status bars)").set_defaults(fn=cmd_status)
    p_stats = sub.add_parser("stats", help="pings, pulls, coverage, top fingerprints")
    p_stats.add_argument("--days", type=int, default=None)
    p_stats.add_argument("--all-hosts", action="store_true",
                         help="aggregate every host dir under the shared state root")
    p_stats.set_defaults(fn=cmd_stats)
    p_log = sub.add_parser("log", help="print recent audit-log records")
    p_log.add_argument("-n", type=int, default=20)
    p_log.set_defaults(fn=cmd_log)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
