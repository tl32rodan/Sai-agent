# vhs script for the README demo GIF — record on the target machine with a
# live LLM endpoint:  vhs docs/demo.tape
Output docs/demo.gif
Set FontSize 16
Set Width 1200
Set Height 640
Set Shell zsh

Type "tmux new -s demo" Enter
Sleep 2s
Type "sai daemon &" Enter
Sleep 1s
Type "make lens" Enter
Sleep 3s
# → sai ▸ exit 2: make · C-b g for why   (one line, status bar)
Type "make lens" Enter
Sleep 3s
# identical failure inside the cooldown window → silence
Ctrl+B
Type "g"
Sleep 8s
# → analyst popup: root cause + concept + one next command
Escape
Sleep 2s
