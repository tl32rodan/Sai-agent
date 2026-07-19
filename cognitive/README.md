# cognitive/ — the P0/P1 cognitive plane

Implements the first two phases of [docs/cognitive-architecture.md](../docs/cognitive-architecture.md):
a **memory-driven judge** behind Sai (P0) and the **feedback → memory loop** (P1).
Everything here is stdlib-only Python (≥ 3.6 — RHEL8 system python3 works),
zero pip installs, usable with or without the Sai daemon.

```
bin/sai-cognitive   the judge: stdin prompt → memory prepended → inner LLM → stdout (+ journal)
bin/sai-observe     batch bridge: pipe ANY text (run-report, log, control_view) → assessment
bin/sai-feedback    P1: mark the last assessment signal|noise → journal + learned memory
bin/sai-reflect     P1: consolidate journal+feedback into memory (dry-run; --apply to write)
seed/flow_doctor/   personality memory template (All-Might shape: understanding/ + journal/)
examples/           a dage rr-v1 run-report to smoke-test against
```

## Quick start (any machine)

```sh
git clone <this repo> && cd sai/cognitive
mkdir -p ~/.config/sai/flow_doctor
cp -r seed/flow_doctor/understanding ~/.config/sai/flow_doctor/   # then EDIT the seeds
cat examples/run-report-failed.json | bin/sai-observe "dage run-report smoke test"
```

Two env knobs (both optional):

| env | default | meaning |
| --- | --- | --- |
| `SAI_COG_MEM` | `~/.config/sai/flow_doctor` | memory dir — one per domain/personality |
| `SAI_COG_BACKEND` | `claude -p` | inner LLM CLI (reads prompt on stdin, answers on stdout) |

## Internal / secure-chamber deployment (no external network)

- **Backend**: point `SAI_COG_BACKEND` at whatever headless agent CLI can reach
  the internal endpoint, e.g. an opencode non-interactive invocation:
  `export SAI_COG_BACKEND="opencode run --quiet"` (any CLI with the
  stdin-prompt → stdout-answer shape works; test it with
  `echo hi | $SAI_COG_BACKEND` first).
- **tcsh flows**: these tools are exec'd scripts, not shell functions — they
  work from tcsh, cron, LSF post-exec, anywhere. The interactive zsh sensor is
  NOT required for the batch path.
- **NFS**: put `SAI_COG_MEM` on the shared NFS home to let the sensing host and
  the LLM-capable host differ (observe on the master host, judge where the
  endpoint is reachable).
- **Data boundary**: the ONLY place observation bytes leave the machine is the
  `SAI_COG_BACKEND` call. Keep it pointed at the sanctioned endpoint; when in
  doubt pipe observations through your redaction first.

## Using it against a dage flow (the intended first domain)

```sh
# one-shot: judge a run-report from the inbox
cat /nfs/.../inbox/run-report-xyz.json | bin/sai-observe "dage run-report, char stage"

# judge a log excerpt (keep it to the interesting tail — the judge reads text, not files)
tail -200 /nfs/proj/char/logs/char-lvf-041.log | bin/sai-observe "LVF char log, lib tsmc_ff_0p88v"

# a whole batch, one assessment
cat inbox/run-report-*.json | bin/sai-observe "tonight's char run-reports"

# teach it while you test (P1 loop)
bin/sai-feedback noise  "lvf license timeouts on Tuesdays are known"
bin/sai-feedback signal "missing .lib template — real config bug, confirmed"
bin/sai-reflect          # review the proposed consolidation
bin/sai-reflect --apply  # accept it into memory
```

Feedback lands in `understanding/learned-feedback.md`, which every subsequent
judgment reads — so the second assessment of the same phenomenon is already
better than the first. That loop is the product; the LLM is a component.

## Wiring the Sai daemon to it (interactive path, optional)

In `~/.config/sai/config.toml`:

```toml
[endpoint]
backend = "command"
command = "sai-cognitive"   # instead of bare "claude -p"
timeout_s = 240
```

`C-b g` (sai pull) then routes through the memory-driven judge. Rollback:
set `command = "claude -p"` back.

## Acceptance (how P0 was verified — repeat it in your environment)

A/B on one observation stream, empty vs seeded memory: the seeded judge must
(1) explicitly suppress the known-noise fingerprint citing the memory,
(2) escalate what memory does not explain, (3) apply owner preferences, and
(4) journal both runs (`journal/*.jsonl`, with `memory_files` provenance).
If both arms answer the same, the memory is not being read — check
`SAI_COG_MEM`.
