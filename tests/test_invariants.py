"""PLAN.md §13 invariants, numbered one-to-one. Details live in the module
tests; this file is the contract."""
from __future__ import annotations

import json

from hypothesis import given, settings, strategies as st

from sai.analyst import build_payload, build_prompt
from sai.config import Config
from sai.daemon import DaemonCore
from sai.fingerprint import fingerprint
from sai.policy import Drop, Policy, Push
from sai.salience import AdviceCandidate, HINT
from tests.conftest import T0, FakeClock, make_tsv

COOLDOWN_S = 900.0
CAPACITY = 3

fp_pool = st.sampled_from(["fp-alpha", "fp-beta", "fp-gamma", "fp-delta"])
steps = st.lists(st.tuples(st.integers(min_value=0, max_value=1800), fp_pool), max_size=50)


def run_policy(sequence) -> list[tuple[float, str]]:
    clock = FakeClock()
    policy = Policy(
        clock=clock, cooldown_s=COOLDOWN_S, bucket_capacity=CAPACITY,
        bucket_refill_s=1200.0, ttl_s=600.0,
    )
    pushed = []
    for delta, fp in sequence:
        clock.advance(delta)
        verdict = policy.decide(AdviceCandidate(fp, HINT, "r1", "exit 1: x", clock.t, "%1"))
        if isinstance(verdict, Push):
            pushed.append((clock.t, fp))
    return pushed


@settings(max_examples=200)
@given(steps)
def test_invariant_1_no_same_fingerprint_push_within_cooldown(sequence):
    pushed = run_policy(sequence)
    by_fp: dict[str, list[float]] = {}
    for t, fp in pushed:
        by_fp.setdefault(fp, []).append(t)
    for times in by_fp.values():
        for earlier, later in zip(times, times[1:]):
            assert later - earlier >= COOLDOWN_S


@settings(max_examples=200)
@given(steps)
def test_invariant_2_rolling_hour_never_exceeds_bucket_capacity(sequence):
    pushed = run_policy(sequence)
    times = [t for t, _ in pushed]
    for t in times:
        assert sum(1 for u in times if t <= u < t + 3600.0) <= CAPACITY


def make_core(clock, config=None):
    pushes: list[tuple[str, str]] = []
    log: list[dict] = []
    core = DaemonCore(
        config or Config(), clock=clock,
        push=lambda pane, text: pushes.append((pane, text)), log=log.append,
    )
    return core, pushes, log


def test_invariant_3_no_candidate_from_blind_span():
    clock = FakeClock()
    core, pushes, log = make_core(clock)
    core.on_pane_output("%1", "\x1b[?1049h")  # vim opens: alternate screen
    core.on_tsv_line(make_tsv(t=clock.t, cmd="make lens", exit=2, pane="%1"))
    assert pushes == []
    verdicts = [r for r in log if r["type"] == "verdict"]
    assert [(v["verdict"], v["reason"], v["rule"]) for v in verdicts] == [
        ("drop", "blind", None)  # rule None: salience was never consulted
    ]
    # …and the policy layer asserts it independently (defense in depth, §9):
    policy = Policy(clock=clock)
    verdict = policy.decide(
        AdviceCandidate("fp-x", HINT, "r1", "exit 2: make", clock.t, "%1"),
        in_blind=True,
    )
    assert isinstance(verdict, Drop) and verdict.reason == "blind"


@settings(max_examples=100)
@given(st.floats(min_value=600.001, max_value=1e6, allow_nan=False))
def test_invariant_4_stale_candidate_never_pushed(age):
    clock = FakeClock(T0 + age)
    policy = Policy(clock=clock, ttl_s=600.0)
    verdict = policy.decide(AdviceCandidate("fp-x", HINT, "r1", "exit 2: make", T0, "%1"))
    assert isinstance(verdict, Drop) and verdict.reason == "ttl"


secret_values = st.text(
    alphabet="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789",
    min_size=12, max_size=48,
)
long_secret_values = st.text(
    alphabet="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789",
    min_size=40, max_size=80,
)


@settings(max_examples=200)
@given(secret_values, long_secret_values)
def test_invariant_5_no_secret_survives_into_analyst_payload(secret, long_secret):
    context = "\n".join([
        "cwd: /home/b/x",
        "$ export AWS_SECRET_ACCESS_KEY=" + secret,
        f"$ curl -H 'Authorization: Bearer {secret}' https://api.example.com",
        f"    DB_PASSWORD={secret}",
        "    -----BEGIN OPENSSH PRIVATE KEY-----",
        f"    {long_secret}",
        "    -----END OPENSSH PRIVATE KEY-----",
        f"    session blob {long_secret}",
    ])
    wire_bytes = json.dumps(build_payload(context, model="some-model"))
    assert secret not in wire_bytes
    assert long_secret not in wire_bytes
    # …and the command-backend prompt honors the same boundary (§16.3):
    prompt = build_prompt(context)
    assert secret not in prompt
    assert long_secret not in prompt


def test_invariant_6_every_push_is_in_the_audit_log():
    clock = FakeClock()
    core, pushes, log = make_core(clock)
    for i, cmd in enumerate(["make lens", "cargo build", "npm test"]):
        clock.advance(30)
        core.on_pane_output("%1", f"{cmd}: fatal error {i}\n")
        core.on_tsv_line(make_tsv(t=clock.t, cmd=cmd, exit=1, pane="%1"))
    assert len(pushes) > 0
    logged_push_texts = {
        r["text"] for r in log if r["type"] == "verdict" and r["verdict"] == "push"
    }
    for _pane, text in pushes:
        assert text in logged_push_texts  # audit log ⊇ what the user saw


def test_invariant_7_fingerprint_invariant_under_volatile_details():
    golden_pairs = [
        (("cat x", ["cat: /home/alice/x: No such file or directory"]),
         ("cat x", ["cat: /tmp/b9/x: No such file or directory"])),
        (("./srv", ["error: worker pid 48213 died"]),
         ("./srv", ["error: worker pid 517 died"])),
        (("./bin", ["Segmentation fault at 0xdeadbeef"]),
         ("./bin", ["Segmentation fault at 0x7ffe12aa"])),
        (("job", ["2026-07-12 10:15:03 ERROR db timeout"]),
         ("job", ["2026-07-13 23:59:59 ERROR db timeout"])),
        (("curl x", ["error: request 182736 failed"]),
         ("curl x", ["error: request 999999 failed"])),
    ]
    for left, right in golden_pairs:
        assert fingerprint(*left) == fingerprint(*right), left
    # …and distinct errors must not collapse into one group:
    assert fingerprint("make", ["Error: Permission denied"]) != fingerprint(
        "make", ["Error: undefined reference to `foo'"]
    )
