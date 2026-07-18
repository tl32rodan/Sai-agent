# Sai (佐為) — ambient terminal advisor

> "I can see the whole board. I just cannot place a single stone."

Sai is a resident, **observe-only** AI advisor for the terminal. It watches shell activity
through native side-channels (zsh hooks + `tmux pipe-pane`), speaks up sparingly at the
right moments, never executes anything, and measures success by whether the human gets
stronger — not by how much work it does on their behalf. Named for Fujiwara-no-Sai in
*Hikaru no Go*: a Go master's spirit who sees every move on the board but cannot touch a
single stone. Full observation, zero actuation, human growth as the objective function.

![demo](docs/demo.gif)

*(demo GIF pending — record with [vhs](https://github.com/charmbracelet/vhs) using
`docs/demo.tape`, or asciinema, on the target machine with a live LLM endpoint)*

## Non-goals

PTY shim / raw byte interposition · OSC 133 segmenter · UserState machine (FLOW/STRUGGLE/…) · escalation ladder · feedback weights / adoption detection · digest mode · tcsh support · multi-session daemon · any cloud service · TUI screen scraping · vim/opencode adapters (deferred to M0.5, see §14).

## Requirements

* zsh
* tmux ≥ 3.2 (for `display-popup`)
* Python ≥ 3.11 (runtime is stdlib-only; `pytest` + `hypothesis` are dev-only)
* an OpenAI-compatible LLM endpoint for the pull path (e.g. llama.cpp `--api` on a Jetson);
  the push path uses no LLM at all

## Install

```sh
git clone https://github.com/tl32rodan/Sai-agent ~/src/sai && cd ~/src/sai
./install.sh          # idempotent: zsh hook snippet + tmux conf include + checks
```

`install.sh` runs `sai init`, which verifies tmux ≥ 3.2 and zsh, writes a default
`~/.config/sai/config.toml` (edit the endpoint URL/model there), installs the zsh hook
snippet and the tmux conf include between idempotent markers, and enables `pipe-pane`
on already-existing panes. Restart zsh (or `source ~/.zshrc`) and reload tmux
(`tmux source-file ~/.tmux.conf`) afterwards.

Then start the daemon inside your tmux session:

```sh
sai daemon &          # or run it in a dedicated window: tmux new-window -d 'sai daemon'
```

## Use

You don't "open" Sai. Work normally. When something looks worth a look — a failure, a
struggle loop, a known error pattern — Sai says one line, at a command boundary, at most
a few times an hour:

```
sai ▸ exit 2 ×3 — same error repeating · C-b g for why
```

* **`prefix + g`** — pull: sends the last few commands + redacted output to your local
  LLM endpoint and shows the analysis in a popup. Pulls are unlimited.
* **`sai stats`** — pings, drops, pulls, pull-after-push rate, blind-zone coverage %,
  top error fingerprints.
* **`sai log`** — tail the audit log (`~/.local/state/sai/pings.jsonl`); every push
  *and* every suppressed push (with reason) is recorded there.
* **`NOTES.md`** — the dogfood diary. This file, not the code, decides M0's verdict.

## Privacy

Privacy is a load-bearing wall, not a feature:

* **Local inference by default** — the only network call is the pull path, to the endpoint
  *you* configure.
* **Output-only capture** — zsh hooks record completed commands; `pipe-pane -O` records
  pane *output*. No keystrokes.
* **Redaction before any byte leaves the machine** — `KEY=`/`TOKEN=`/`SECRET=`/`PASSWORD=`/
  `AWS_*=` values, `Authorization:` headers, SSH private key blocks, and long base64/hex
  runs are replaced with `[REDACTED]` (property-tested).
* **Blind zones are honored** — full-screen TUI sessions (vim, htop, …) toggle the
  alternate screen; Sai excludes those spans from its buffers and its rules entirely.
  The sensor's limit and the politeness boundary coincide by design.

## Development

```sh
pip install -e '.[dev]'
pytest
```

The pure core (`events`, `fingerprint`, `salience`, `policy`, `redact`) contains no IO and
no clock — see `tests/test_purity.py`. The seven invariants of PLAN.md §13 exist one-to-one
in `tests/test_invariants.py`.

## License

MIT — see [LICENSE](LICENSE).
