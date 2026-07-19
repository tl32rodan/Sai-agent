# Cognitive architecture — Sai's next chapter

> Status: design (2026-07). This is a forward-looking architecture, not the shipped M0.
> It deliberately **inverts** two M0 stances (LLM-free push; feedback/learning as non-goals).
> M0 remains the cheap, always-on floor underneath it.

## 1. The pivot

M0 Sai is a **fixed-rule reflex advisor**: sensor → hardcoded salience (R1/R2/R3) → LLM-free
one-line push, with an LLM only on the on-demand pull. It is fast, cheap, and dumb on purpose.

The next chapter makes Sai **cognitive-agent-first**: behind each daemon sits a **harnessed
agent with an iterable, per-domain cognitive system** — memory + skills + *learned* salience,
self-updating. "What is worth speaking about" becomes **learned and remembered**, not hardcoded.
The reflex rules are demoted to a cheap **pre-filter** and **cold-start floor**.

Two invariants carry over unchanged:

- **Observe-only.** Sai assesses and notifies; it never actuates the flow. Humans and the
  control plane act.
- **Domain-agnostic substrate.** The core knows no application vocabulary. A concrete domain
  (e.g. the `dage` derived-asset engine) is *one adapter*, never the definition.

## 2. Thesis — the inversion

1. **Salience = learned judgment, not rules.** Memory drives what surfaces. R1/R2/R3 become a
   funnel that *proposes*; the cognitive plane *disposes* and *learns*.
2. **A Sai daemon = a harnessed agent bound to one observation stream**, with its own
   memory/journal — an iterable cognitive system that gets better at *its* slice over time.
   Many daemons = many agents over many regions: **scatter-gather with learned, per-region
   understanding.**

## 3. Two planes

- **Reflex plane** — thin, LLM-free, always-on; the cold-start floor and the cost valve.
  Sensor substrate → cheap gating (any anomaly signal? dedup? rate budget) → **funnels
  candidates** upward. It is no longer the decider; it exists so the LLM is *not* called per
  log line.
- **Cognitive plane** — the centerpiece. A harnessed agent with memory + skills that:
  1. judges salience using **learned domain understanding**;
  2. interprets and aggregates across the stream;
  3. emits a **preliminary assessment** (labeled as such);
  4. decides notify / root-cause;
  5. **updates its own memory** — the iteration.

## 4. The cognitive system (the iterable core)

- **Memory** — accumulated domain understanding: normal-vs-abnormal, known-noise fingerprints,
  past root-causes, owner preferences.
- **Skills** — domain procedures: root-cause recipes, stage-specific checks.
- **Learned salience** — memory + human feedback refine what to surface. This delivers exactly
  what M0 deferred (feedback / adoption), but via **memory**, not hardcoded weights.
- **Iteration** — a consolidation loop turns outcomes ("was this useful or noise?") into updated
  understanding.

The concrete harness for all four already exists in the sibling **All-Might** project
(personalities, `understanding/` + `journal/` memory, skills, a `/reflect` consolidation loop).
A Sai daemon is therefore an All-Might **personality** (e.g. `flow_doctor`) **bound to a Source
stream**. Sai contributes the sensory substrate, the timing/politeness discipline, and the
daemon lifecycle; All-Might contributes the cognition.

## 5. Role boundaries

| Actor | Owns |
| --- | --- |
| **Domain adapter** (e.g. dage + its container) | streams; fills **opaque `labels`** (k/v) and a configurable `group_by`; owns domain truth. The core learns no domain axes. |
| **Sai daemon** (cognitive observation agent) | reflex funnel → cognitive plane → memory update. Observe-only. |
| **Control plane / humans** | act — control, and the feedback that trains the agent. |

Pushing "observe global state" to the edge (per-region daemons) means the control plane no
longer has to centralize-pull everything just to know where to act: it consumes **aggregated
assessments** instead. dage says *state* (MISSING / STALE / IN-FLIGHT / FRESH); Sai says the
*anomaly semantics on top of that state* — the pattern, its coverage, a preliminary root cause.

An observation this layer is meant to produce, unprompted:

> Of 100 libraries: 35 in the LVF branch's characterization stage show a similar error;
> 50 are clean; 15 are still in the LVF TCL-preparing stage (unknown). — with a preliminary
> root-cause hypothesis and, optionally, a note mailed to the owners.

That is **agency inversion**: the human is *told* what is happening, with an initial assessment,
instead of polling for it.

## 6. The observation contract (generic)

Events carry **opaque, structured `labels`** (a k/v map) plus a configurable `group_by`, never
named domain axes. Aggregation (`GROUP BY (stage, branch, error-fingerprint)` in the example
above) operates on those labels. A domain adapter is what maps its world onto them — for dage,
its run-report `Manifest` already carries coordinate-tagged leaves, a cleaner source than raw
log parsing. Evidence travels as a **lazy pointer** (`context_ref`: an NFS path + offset range),
never inlined — mirroring dage's KB-sized reports that carry `log_path`, not log bytes.

This contract, and the pluggable `Source` interface that feeds it, is the **substrate** (below).
It is plumbing for the cognitive plane, not the point.

## 7. Deployment topology (secure-chamber PoC)

- **Reflex funnel (no LLM)** runs on the prod/master host — zero endpoint dependency.
- **Cognitive plane (LLM + memory)** runs where the IT endpoint is reachable — a specific
  RHEL8.10 host, or via `backend = command` pointed at a headless **opencode** invocation.
- **Shared NFS** carries both state and the agent's memory, so the sensing host and the
  LLM-capable host can differ.
- The redaction boundary ("the only place bytes leave") maps onto "only the IT endpoint."

## 8. Non-goals (for this chapter)

- **An LLM call per log line.** The reflex funnel + memory-based cheap suppression are
  load-bearing; cost is bounded by construction.
- **Actuating the flow.** Notification and root-cause analysis only; control stays with the
  control plane.
- **Trusting the judge blindly.** Assessments are *preliminary*; a human-feedback loop trains
  the memory; hallucination is contained by the observe-only boundary.
- **A cold-start oracle.** A fresh daemon has no learned memory and falls back to the reflex
  rules until understanding accumulates.

## 9. Phasing

- **P0 (minimal code)** — `backend = command` → a harnessed `flow_doctor` personality *with
  memory*, in place of bare `claude -p`. The reflex plane becomes a pre-filter. The smallest
  real "cognitive agent behind the daemon"; demoable on a single host where Sai and All-Might
  coexist.
- **P1** — the cognitive loop: memory-driven salience judgment + **outcome recording** (the
  agent notes whether a surfaced item was signal or noise, updating understanding).
- **P2** — the generic `Source` substrate + `labels` feeding the cognitive plane; cross-stream
  aggregation as an **agent skill**.
- **P3** — report-to-control-plane + notify/root-cause actions; a dage `container` declares its
  streams / labels / error-patterns (and an agent-skill reference).

## 10. Open questions

- **Memory granularity** — per-daemon memory vs a shared domain memory across daemons; how does
  scatter-gather *learning* consolidate?
- **Handoff budget** — how cheap must the reflex funnel be to keep LLM cost bounded?
- **Replace vs rerank** — does the cognitive judge replace R1/R2/R3 or rerank their proposals?
  (Working answer: reflex proposes, cognitive disposes and learns.)
- **Feedback ingestion** — how does a human's adoption/rejection signal enter memory?

## 11. Smallest proof of the thesis

On one host with Sai + All-Might: wire `backend = command` to a personality seeded with a small
memory (one known-noise fingerprint marked *ignore*, one owner preference). Feed a scripted
failure stream. Show the daemon's assessment **diverges from raw R1/R2/R3** — it suppresses the
known-noise fingerprint, escalates a novel one, and **writes an outcome to its journal**. One run
demonstrates memory-driven, self-updating salience, without building the full substrate.
