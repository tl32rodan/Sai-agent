"""`sai stats` — the measurement half of M0 (PLAN.md §11)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Sequence

PULL_AFTER_PUSH_S = 120.0


def compute_stats(records: Sequence[dict], *, now: float, days: int | None = None) -> dict:
    if days is not None:
        cutoff = now - days * 86400
        records = [r for r in records if r.get("t", 0) >= cutoff]

    verdicts = [r for r in records if r.get("type") == "verdict"]
    pushes = [r for r in verdicts if r.get("verdict") == "push"]
    drops = [r for r in verdicts if r.get("verdict") == "drop"]
    pulls = [r for r in records if r.get("type") == "pull"]

    push_times = [r["t"] for r in pushes]
    pulls_after_push = sum(
        1 for p in pulls
        if any(0 <= p["t"] - t <= PULL_AFTER_PUSH_S for t in push_times)
    )

    failed_cmds = [r for r in records if r.get("type") == "cmd" and r.get("exit") != 0]
    fp_counts = Counter(r["fp"] for r in failed_cmds if r.get("fp"))
    fp_example = {r["fp"]: r.get("cmd", "?") for r in failed_cmds if r.get("fp")}

    return {
        "pushes": len(pushes),
        "pushes_by_rule": dict(Counter(r.get("rule") or "?" for r in pushes)),
        "drops": len(drops),
        "drops_by_reason": dict(Counter(r.get("reason") or "?" for r in drops)),
        "pulls": len(pulls),
        "pulls_after_push": pulls_after_push,
        "coverage_by_day": _coverage_by_day(records),
        "top_fingerprints": [
            (fp, count, fp_example.get(fp, "?")) for fp, count in fp_counts.most_common(10)
        ],
    }


def _coverage_by_day(records: Sequence[dict]) -> list[tuple[str, float, float, float]]:
    """(day, coverage_ratio, active_s, blind_s) per day — an approximation:
    active span = first-to-last record; blind seconds summed per pane."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if "t" in r:
            by_day[datetime.fromtimestamp(r["t"]).strftime("%Y-%m-%d")].append(r)

    out = []
    for day in sorted(by_day):
        day_records = sorted(by_day[day], key=lambda r: r["t"])
        active_s = day_records[-1]["t"] - day_records[0]["t"]
        blind_s = 0.0
        entered: dict[str, float] = {}
        for r in day_records:
            if r.get("type") != "blind":
                continue
            pane = r.get("pane", "?")
            if r.get("state") == "enter":
                entered.setdefault(pane, r["t"])
            elif r.get("state") == "exit" and pane in entered:
                blind_s += r["t"] - entered.pop(pane)
        for start in entered.values():  # unclosed blind span: count to end of day's records
            blind_s += day_records[-1]["t"] - start
        coverage = 1.0 - min(blind_s / active_s, 1.0) if active_s > 0 else 1.0
        out.append((day, coverage, active_s, blind_s))
    return out


def render_stats(stats: dict, *, days: int | None = None, hosts: int | None = None) -> str:
    scope = f"last {days} day(s)" if days is not None else "all time"
    if hosts is not None:
        scope += f" · across {hosts} host(s)"
    lines = [f"sai stats — {scope}"]

    by_rule = " · ".join(f"{k} {v}" for k, v in sorted(stats["pushes_by_rule"].items()))
    lines.append(f"  pushes: {stats['pushes']}" + (f"   ({by_rule})" if by_rule else ""))
    by_reason = " · ".join(f"{k} {v}" for k, v in sorted(stats["drops_by_reason"].items()))
    lines.append(f"  drops:  {stats['drops']}" + (f"   ({by_reason})" if by_reason else ""))

    rate = (
        f"{stats['pulls_after_push']}/{stats['pushes']}"
        f" ({stats['pulls_after_push'] / stats['pushes']:.0%})"
        if stats["pushes"] else "n/a (no pushes)"
    )
    lines.append(f"  pulls:  {stats['pulls']}   pull-after-push: {rate}")

    lines.append("  coverage (approx: 1 − blind/active span per day):")
    if stats["coverage_by_day"]:
        for day, cov, active_s, blind_s in stats["coverage_by_day"]:
            lines.append(
                f"    {day}  {cov:.0%}  (active {active_s / 3600:.1f}h · blind {blind_s / 3600:.1f}h)"
            )
    else:
        lines.append("    no data yet")

    lines.append("  top error fingerprints:")
    if stats["top_fingerprints"]:
        for fp, count, cmd in stats["top_fingerprints"]:
            lines.append(f"    {fp}  ×{count}  {cmd}")
    else:
        lines.append("    none yet")
    return "\n".join(lines)
