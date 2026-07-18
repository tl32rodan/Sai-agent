# Sai (佐為) — Ambient Terminal Advisor

## M0 Implementation Plan

> "I can see the whole board. I just cannot place a single stone."

**Status:** ready for implementation
**Owner:** Brian
**Target environment:** personal machine — zsh + tmux (≥ 3.2) + local OpenAI-compatible LLM endpoint (e.g. llama.cpp on Jetson)
**License:** MIT

### How to use this document (instructions for the implementing agent)

1. Read §1–§4 before writing any code. When an implementation choice conflicts with §2 (Philosophy), §2 wins.
2. Follow strict TDD: for each module, write the test list first, then red-green-refactor. The invariants in §13 must exist as automated tests, mapped one-to-one.
3. When anything is ambiguous, ask the owner — do not assume. §15 lists known open questions; add yours there.
4. Respect the non-goals in §4.2 absolutely. Building anything on that list is a defect, not initiative.

## 1. Thesis

Sai is a resident, observe-only AI advisor for the terminal. It watches shell activity through native side-channels, speaks up sparingly at the right moments, never executes anything, and measures success by whether the human gets stronger — not by how much work it does on their behalf.

The name comes from Fujiwara-no-Sai (藤原佐為) in *Hikaru no Go*: a Go master's spirit who sees every move on the board but cannot touch a single stone. He can only speak — and his student becomes strong precisely because Sai advises instead of playing. The name is the product contract: full observation, zero actuation, human growth as the objective function.

## 2. Philosophy (normative — must survive every refactor)

**Proactive attention, not proactive action.** Existing AI agents are autonomous in what they execute. Sai is autonomous only in *when it speaks*. Its sole actuator is a message. This collapses the entire sandbox/permission problem and replaces it with a harder, more interesting one: the economics of human attention.

**Inversion of the host relationship.** Chat apps and coding agents are reactive tenants — you enter their REPL and ask. Sai is a resident layer: your shell and workflow are unchanged; it observes from the side and occasionally taps your shoulder. The user never "opens Sai."

**Trust is asymmetric (Clippy's law).** A quiet week merely delays value; an annoying week kills the product permanently. Every default in this spec is therefore conservative. When in doubt, stay silent.

**Teachable moments.** Learning happens at impasses. Sai's job is to detect the impasse (a failure, a struggle loop) and offer the *why*, not just the *what*. The long-term north-star metric is: the recurrence rate of identical error fingerprints declines over time. M0 does not implement this metric, but every data structure must not preclude it.

**Privacy is a load-bearing wall, not a feature.** An agent that observes everything cannot bolt trust on afterwards. Concretely in M0: local inference by default, output-only capture (no keystrokes), regex redaction before any byte leaves the machine, and honored blind zones.

**Blind zones are a feature.** Full-screen TUI sessions (vim, htop, opencode) are unobservable through this sensor — and they are exactly the moments the user must not be interrupted. The sensor's limit and the politeness boundary coincide by design. Do not fight this; measure it (§11 coverage metric).

## 3. M0 hypothesis and kill criteria

M0 validates the interaction model, not the architecture. Single hypothesis:

> "Being observed by an occasionally-speaking advisor is something I still want turned ON after 14 days of real use."

* **Green light:** still enabled after 14 days, AND the diary (`NOTES.md`) contains at least one "glad it spoke" moment per week.
* **Red light:** the owner turned it off and did not miss it → the interaction model needs rework. This is a cheap, intended failure mode; discovering it before building M1 is the entire point of M0.

Everything in scope serves this hypothesis. Anything that does not is out.

## 4. Scope

### 4.1 In scope (core logic ≤ ~400 LOC, runtime stdlib-only)

1. **Sensors** — zsh `preexec`/`precmd` hooks emitting command events; `tmux pipe-pane -O` capturing per-pane output; alternate-screen escape markers parsed for blind-zone tracking.
2. **Salience** — three pure rules, zero LLM in the push path.
3. **Politeness** — three constants: boundary-only timing, per-fingerprint cooldown, global rate limit.
4. **Voice** — push: a single one-line volume via `tmux display-message`; pull: a keybinding that sends redacted recent context to one configurable LLM endpoint and renders the reply in `tmux display-popup`.
5. **Measurement** — `sai stats` (pings, pulls, coverage %, top fingerprints) plus a human-written diary.

### 4.2 Non-goals (copy into README verbatim)

PTY shim / raw byte interposition · OSC 133 segmenter · UserState machine (FLOW/STRUGGLE/…) · escalation ladder · feedback weights / adoption detection · digest mode · tcsh support · multi-session daemon · any cloud service · TUI screen scraping · vim/opencode adapters (deferred to M0.5, see §14).

## 5. Architecture

```
zsh hooks ──────────► events.tsv ─┐
                                   ├─► sai daemon ──► push: tmux display-message
tmux pipe-pane -O ─► out-%N.log ──┘        │           log:  pings.jsonl
                                            └─► state: ring buffers, budgets

user keybinding ─► sai pull ─► gather context ─► redact ─► LLM endpoint
                                                              │
                                              tmux display-popup ◄┘
```

**Ports & adapters.** The pure core (`events`, `fingerprint`, `salience`, `policy`, `redact`) contains no IO, no `time.time()`, no `open()` — clock and filesystem are injected. The IO shell (`daemon`, `analyst`, tmux calls) stays thin and is smoke-tested only. Sensors are adapters emitting a single shared event schema; future sensors (nvim RPC, opencode SSE) must be pure additions with zero core changes.

**Data-flow decisions (deliberate, do not "improve"):**

* **Shell writes TSV, not JSON.** JSON escaping in shell is a bug farm. Hooks append tab-separated fields with the command base64-encoded; the daemon normalizes to canonical JSONL. Shell stays dumb and fast; parsing lives in tested Python.
* **The daemon stamps output arrival times itself.** `pipe-pane` gives raw bytes with no timestamps. The daemon tails each per-pane log (poll ≈ 0.2 s), stamps lines on read into a per-pane ring buffer (default 500 lines). This avoids `ts`/moreutils dependencies and extra per-pane processes.
* **Single attached tmux client assumed.** `display-message` targets the session; multi-client routing is out of scope.

## 6. Event schema

Shell-side raw record (TSV, one line per event, `~/.local/state/sai/events.tsv`):

```
<epoch_float>  <kind>  <exit>  <dur_s>  <cwd>  <pane_id>  <b64(cmd)>
```

* `preexec` stores `(epoch, cmd, cwd, pane)` in shell variables; `precmd` emits one complete record with `exit=$?` and computed duration (`zmodload zsh/datetime`, `$EPOCHREALTIME`). One record per completed command; no pending/partial records in the file.
* Daemon-normalized canonical form (internal + `pings.jsonl`):

```json
{"t": 1786500000.123, "type": "cmd", "cmd": "make lens", "cwd": "/home/b/x",
 "exit": 2, "dur_s": 94.2, "pane": "%3", "tail": ["...last output lines..."]}
{"t": 1786500100.0, "type": "blind", "pane": "%3", "state": "enter"}
```

* **Output tail:** on command completion, snapshot ring-buffer lines whose arrival time ∈ `[start, end + 0.5 s]`, capped at 40 lines / 8 KB, ANSI-stripped before any pattern matching.
* **Blind zones:** scan raw pane bytes for alternate-screen toggles — `ESC [ ? 1049 h/l`, plus legacy `? 47` and `? 1047` variants. Emit `blind enter/exit` events; exclude blind spans from ring buffers and from salience entirely.

## 7. Fingerprinting (pure)

`fingerprint(cmd, tail) -> str` (12 hex chars of sha1):

1. Strip ANSI escapes.
2. In the first error-looking line of the tail plus the first token of the command, replace: hex addresses (`0x[0-9a-f]+`), absolute paths, PIDs, timestamps, and integers longer than 3 digits with placeholders.
3. Hash the normalized string.

Deterministic; unit-tested with golden cases (same error, different paths/PIDs → same fingerprint). This is Sentry-style error grouping in miniature.

## 8. Salience rules (pure)

Input: completed command event (+ recent history). Output: `AdviceCandidate {fingerprint, severity, evidence} | None`.

* **R1** `exit != 0` → HINT.
* **R2** within the last 5 commands, ≥ 2 failures whose pairwise similarity ≥ 0.6 (`difflib.SequenceMatcher.ratio`, stdlib) → WARN. (struggle loop)
* **R3** tail matches error patterns (`Traceback|Permission denied|command not found|undefined reference|Segmentation fault|No such file`) → HINT; WARN if combined with R1.

No LLM anywhere in the push path.

## 9. Policy (pure, injected clock)

* **Boundary-only:** decisions are evaluated only on completed command events (structurally guaranteed by the sensor — never fires mid-command).
* **Cooldown:** same fingerprint silenced for 15 min after a push.
* **Rate limit:** token bucket, capacity 3, refill 1 token / 20 min. Applies to pushes only; pulls are unlimited.
* **TTL:** a candidate older than 10 min is dropped, never pushed. A stale hint is worse than silence.
* **Blind:** events inside blind spans never become candidates (enforced upstream, asserted here too).

Output verdict: `Push(text) | Drop(reason)`. Every verdict (including drops, with reason) is appended to `pings.jsonl` — the L0 audit log.

## 10. Voice

**Push** — rule-templated, one volume, ≤ 80 chars:

```
tmux display-message -t <pane> "sai ▸ exit 2 ×3 — same error repeating · C-b g for why"
```

No LLM text in pushes. The push only says *that* something is worth a look; the pull says *what*.

**Pull** — tmux binding (default `prefix + g`) runs `sai pull`:

1. Gather the last 5 completed commands with tails + cwd + fingerprint repeat counts.
2. Apply redaction (§10.1).
3. POST to `{endpoint}/v1/chat/completions` with `SAI_MODEL`, the system prompt from Appendix A, 30 s timeout.
4. Render reply in `tmux display-popup` (requires tmux ≥ 3.2; `sai init` must verify the version and fail loudly with instructions if older). On endpoint failure, show a graceful one-line error — never a stack trace in the popup.

### 10.1 Redaction (pure, applied to every byte leaving the machine)

Replace with `[REDACTED]`: values following `KEY=`, `TOKEN=`, `SECRET=`, `PASSWORD=`, `PASS=`, `API_KEY=`, `AWS_[A-Z_]+=`; `Authorization:` header values; SSH private key blocks; any base64/hex run longer than 32 chars. Property-tested (§13, invariant 5).

## 11. Measurement

`sai stats [--days N]` prints: pushes emitted (per rule), drops (per reason), pulls, pull-after-push rate (pulls within 2 min of a push ÷ pushes — the M0 proxy for "was that ping useful"), coverage % (1 − blind seconds ÷ active span per day; approximation is fine, label it as such), top 10 fingerprints by count.

`NOTES.md` — owner-written diary. Ship a template with three columns: date / what Sai said / was it welcome. This file, not the code, decides M0's verdict (§3).

## 12. Repository layout

```
sai/
  README.md            # thesis (one paragraph), demo GIF, non-goals verbatim, install
  PLAN.md              # this document
  docs/manifesto.md    # owner-authored; agent must NOT generate marketing prose here
  install.sh           # idempotent: zsh hook snippet + tmux conf include
  sai/
    __init__.py
    events.py          # schema, TSV→JSONL normalization, ring buffer
    fingerprint.py     # pure
    salience.py        # pure
    policy.py          # pure, injected clock
    redact.py          # pure
    daemon.py          # IO shell: tail files, stamp, decide, tmux
    analyst.py         # LLM adapter (pull path only)
    stats.py
    cli.py             # sai init|daemon|pull|stats|log
  shell/sai.zsh        # preexec/precmd hooks
  tmux/sai.conf        # pipe-pane hooks for new panes + prefix-g binding
  tests/
```

`sai init` installs the zsh snippet and tmux conf include, verifies tmux ≥ 3.2 and zsh, enables `pipe-pane` on already-existing panes (`tmux list-panes -a` loop); `tmux/sai.conf` uses `set-hook` (`after-new-window`, `after-split-window`, `after-new-session`) so new panes are piped automatically.

## 13. Engineering constraints and invariants

**Constraints:** Python ≥ 3.11 (for `tomllib`). Runtime dependencies: stdlib only — zero third-party packages. Dev-only dependencies allowed: `pytest`, `hypothesis`. Config: `~/.config/sai/config.toml` (Appendix B). State: `~/.local/state/sai/`. Conventional commits; each milestone is one PR-sized changeset; tests committed before or with the code they test.

**Invariants** — each must exist as an automated (property or table-driven) test, numbered identically:

1. No two pushes with the same fingerprint within the cooldown window.
2. Pushes in any rolling hour ≤ token-bucket capacity.
3. No candidate is ever produced from an event inside a blind span.
4. A candidate older than TTL is never pushed.
5. For hypothesis-generated inputs containing configured secret patterns, no secret substring survives in the analyst request payload.
6. Every push also appears in `pings.jsonl` (audit log is a superset of what the user saw).
7. `fingerprint()` is invariant under path / PID / address / timestamp substitution (golden cases).

**Definition of done per module:** pure core at ~100 % branch coverage; no `time.time()`/`open()`/subprocess in pure modules (enforce with a grep-based test); functions small and intention-named; IO shell covered by one smoke test each.

## 14. Milestones

**M0.a — pipeline (weekend 1).** Hooks → TSV → daemon → salience → `display-message`. Acceptance: replaying a scripted event file through the daemon core produces exactly the expected verdict sequence (golden test); live smoke: an induced `make` failure produces exactly one ping; an identical failure 1 min later produces none (cooldown).

**M0.b — pull + polish (weekend 2).** Pull path with redaction, `sai stats`, README with GIF (vhs or asciinema), publish to GitHub. Acceptance: `prefix+g` after a failure shows an LLM analysis popup; a planted `AWS_SECRET_ACCESS_KEY` in context never reaches the request payload (test); `sai stats` prints coverage %.

**M0.c — dogfood (days 1–14).** No new features. Deliverables: `NOTES.md` diary, a stats snapshot, and a green/red verdict per §3.

**M0.5 — conditional, not scheduled.** opencode SSE sensor adapter (reusing the opencode-pulse event stream) only if M0.c shows coverage < 50 %. Trigger on data, not on enthusiasm.

## 15. Open questions (implementing agent: ask, don't assume)

1. LLM endpoint URL and model name for the pull path (llama.cpp on Jetson? port?). *(Model: resolved 2026-07-18 — left empty by owner decision; the request omits the field and the endpoint's loaded model is used. URL: still open, set in `config.toml` at deploy time.)*
2. Confirm zsh is the only shell on the target machine for M0 (tcsh is a non-goal).
3. Preferred keybinding if `prefix + g` collides with an existing tmux binding.
4. Should `pings.jsonl` drops be pruned/rotated, or is append-forever fine for 14 days? (Default: append-forever.)

### 15.1 Questions added during implementation (agent → owner; defaults chosen, all reversible in code review)

5. **Invariant 2 vs token-bucket burst math.** A bucket with capacity 3 that starts full and refills 1 token / 20 min permits up to 6 pushes in the *first* rolling hour (3 burst + 3 refilled), which would violate invariant 2 as written ("pushes in any rolling hour ≤ token-bucket capacity"). Per §2 (conservative defaults; when in doubt, stay silent), the implementation enforces **both** the token bucket **and** a hard rolling-one-hour cap equal to the bucket capacity. The stricter of the two wins; invariant 2 holds exactly as written. If the burst-friendlier pure-bucket behavior was intended, delete the rolling-window check in `policy.py` and relax the invariant-2 test.
6. **R2 on a successful command.** Should a *successful* command completion fire the struggle-loop rule because two earlier commands in the window failed? Chosen default: no — the current event must itself be a failure for R2 (a struggle that just ended in success needs no ping). Table-tested in `tests/test_salience.py`.
7. **"First error-looking line" (§7).** Defined as: first tail line matching the §8 error patterns or `error|fatal|fail` (case-insensitive); falling back to the last non-empty tail line, else the empty string. Golden-tested.
8. **Long-run redaction false positives.** The ">32 char base64/hex run" rule redacts some long paths and identifiers too. Accepted: conservative beats leaky (§2 privacy). Revisit only if pull-path answers degrade in practice.
9. **License mismatch.** *(Resolved 2026-07-18: owner chose MIT; the Apache-2.0 `LICENSE` file was replaced.)*

## Appendix A — Analyst system prompt (pull path)

> You are Sai, a resident terminal advisor. You can see the user's recent commands and their output, but you cannot execute anything — like a Go master who may speak but never place a stone. Diagnose the most likely root cause, teach the underlying concept in two to four sentences so the user is stronger next time, then suggest one concrete next command. Never claim to have run anything. Be brief; the user is mid-work.

## Appendix B — Default `config.toml`

```toml
[endpoint]
url = "http://jetson.local:8080"   # OpenAI-compatible
model = ""                         # empty: use whatever model the endpoint has loaded
timeout_s = 30

[policy]
cooldown_min = 15
bucket_capacity = 3
bucket_refill_min = 20
ttl_min = 10

[capture]
tail_lines = 40
tail_bytes = 8192
ring_lines = 500

[keys]
pull = "g"    # bound under tmux prefix
```
