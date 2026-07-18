# Sai (佐為) — zsh sensor. Observe-only: records completed commands, never keystrokes.
# Emits one TSV record per completed command (PLAN.md §6):
#   <epoch_float> <kind> <exit> <dur_s> <cwd> <pane_id> <b64(cmd)>
# The command is base64-encoded so the shell never has to escape anything.

zmodload zsh/datetime 2>/dev/null || return 0
autoload -Uz add-zsh-hook

# Host-scoped: on an NFS home, every host writes only its own subdir (§16.5).
: ${SAI_STATE_DIR:="${XDG_STATE_HOME:-$HOME/.local/state}/sai/${HOST%%.*}"}
[[ -d "$SAI_STATE_DIR" ]] || mkdir -p "$SAI_STATE_DIR" 2>/dev/null

typeset -g _sai_cmd="" _sai_start=0 _sai_cwd=""

_sai_preexec() {
  _sai_cmd="$1"
  _sai_start="$EPOCHREALTIME"
  _sai_cwd="$PWD"
}

_sai_precmd() {
  local ret=$?
  [[ -n "$_sai_cmd" ]] || return 0
  local end="$EPOCHREALTIME"
  local -F dur=$(( end - _sai_start ))
  # a tab or newline in the cwd would corrupt the TSV record
  local cwd="${_sai_cwd//[$'\t\n']/ }"
  # base64 wraps at 76 cols; strip all newlines so the record stays one line.
  local b64
  b64="$(print -rn -- "$_sai_cmd" | base64)"
  b64="${b64//$'\n'/}"
  LC_ALL=C printf '%s\tcmd\t%d\t%.3f\t%s\t%s\t%s\n' \
    "$end" "$ret" "$dur" "$cwd" "${TMUX_PANE:-none}" "$b64" \
    >> "$SAI_STATE_DIR/events.tsv" 2>/dev/null
  _sai_cmd=""
  return 0
}

add-zsh-hook preexec _sai_preexec
add-zsh-hook precmd _sai_precmd

# Lazy per-host daemon start (§16.5): ssh-ing into any host of the fleet
# brings its daemon up. The pidfile lives on host-local tmpfs, so the cheap
# existence check below covers the common case; `sai ensure-daemon` does the
# real liveness check. Opt out with SAI_NO_AUTOSTART=1.
if [[ -z "$SAI_NO_AUTOSTART" ]] && command -v sai >/dev/null 2>&1; then
  _sai_rt="${SAI_RUNTIME_DIR:-/tmp/sai-$UID}"
  [[ -e "$_sai_rt/daemon.pid" ]] || sai ensure-daemon >/dev/null 2>&1 &!
fi
