# Shell support — why the sensor is zsh-only

Sai's command sensor (`shell/sai.zsh`) is deliberately zsh-only. This note explains
what the sensor needs, why zsh provides it natively, and why bash and csh/tcsh do not.
tcsh support is an explicit non-goal (PLAN.md §4, §12).

## What the sensor needs

One record per *completed* command requires hooking two moments and capturing three
kinds of data (PLAN.md §6):

| Moment | Data captured |
| --- | --- |
| **Before execution** (command entered, not yet run) | command text, start time, cwd, pane |
| **After execution** (command finished, before next prompt) | exit code, computed duration |

Plus: a sub-second clock (for duration), a per-command function hook mechanism, and
raw-safe string output (the command is base64-encoded so the shell never escapes
anything — PLAN.md §6).

## zsh — has all of it, natively

* `add-zsh-hook preexec` fires **before** execution and receives the command line as
  `$1`; the hook stashes `(EPOCHREALTIME, cmd, cwd, TMUX_PANE)`.
* `add-zsh-hook precmd` fires **after** execution / before the next prompt; it reads
  `$?` for the exit code and computes the duration.
* `zmodload zsh/datetime` exposes `$EPOCHREALTIME` — a float-seconds clock.

No external dependencies, no shims. The shell stays "dumb and fast"; all parsing lives
in tested Python (PLAN.md §6).

## bash — not impossible, but not native (so: unsupported)

bash has only **half** of what's needed:

* **After-execution: yes.** `PROMPT_COMMAND` runs before each prompt — the moral
  equivalent of `precmd`, and it can read `$?`.
* **Before-execution: no native hook.** bash has no `preexec`. There is no built-in way
  to observe "the command about to run" with its text.

The community workaround is [`bash-preexec`](https://github.com/rcaloras/bash-preexec),
which *emulates* preexec/precmd on top of the `DEBUG` trap + `PROMPT_COMMAND`. It works,
but it is a sourced third-party dependency and the `DEBUG` trap has sharp edges: it
fires for pipeline elements, subshells, and command substitutions, so faithfully
reconstructing "one record per interactive command" takes care.

Sai's design goal is a **zero-dependency, robust** sensor, so bash is left unsupported
rather than shipping and maintaining that shim. This is a scope decision, not a hard
impossibility — a `shell/sai.bash` built on `bash-preexec` is conceivable future work.

## csh / tcsh — genuinely hard (non-goal)

* **csh (original):** no pre/post-command hooks at all, no functions (only aliases), no
  sub-second clock, and very weak string handling. There is nothing to hook — this one
  really cannot be done cleanly.
* **tcsh:** *does* have the `precmd` and `postcmd` special aliases, so it is technically
  hookable — but the friction is high:
  * `postcmd` does **not** hand you the command text; you would have to parse history.
  * no `EPOCHREALTIME`-grade timing for durations.
  * no functions and awkward quoting/escaping; base64-encoding a command line means
    shelling out and fighting csh's string rules.

The payoff-to-friction ratio is poor, so **tcsh is an explicit non-goal** (PLAN.md §4,
§12). Capturing a tcsh session would mean giving up the structured command-event stream
and falling back to pure output capture (PTY / `pipe-pane`) — which is itself on the
non-goals list ("PTY shim / raw byte interposition").

## Practical guidance

* **Personal / dev machine:** run zsh (as login shell, or launched inside tmux). Full
  fidelity: command events + output tail + pushes + status + pull.
* **Environments locked to tcsh (e.g. many EDA/CAD setups):** the sensor does not apply.
  The output-only surfaces (`pipe-pane`) could in principle be reused, but the salience
  rules that depend on exit codes and durations (R1/R2) would go dark — effectiveness is
  substantially reduced by design, not by accident.
