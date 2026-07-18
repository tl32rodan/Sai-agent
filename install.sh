#!/bin/sh
# Sai installer — idempotent; safe to re-run. Delegates to `sai init`, which
# installs the zsh hook snippet + tmux conf include between marker blocks and
# verifies tmux ≥ 3.2 and zsh (PLAN.md §12).
set -eu
REPO="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
SAI_REPO="$REPO" exec python3 -c '
import os, sys
sys.path.insert(0, os.environ["SAI_REPO"])
from sai.cli import main
sys.exit(main(["init"]))
'
