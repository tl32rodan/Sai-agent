# Known noise — do not escalate

(Template — replace with your flow's real known-noise fingerprints. Be
specific: command/step, exit code, message, WHY it is noise, and the
threshold beyond which it stops being noise.)

- Example: the command `flaky-probe` failing with **exit 3** and the message
  `probe: NFS attribute cache miss (transient)` is **known noise**: it races
  the NFS attribute-cache window (acdirmax ~30s) and self-heals next tick.
  Suppress unless it exceeds ~20 failures/hour or the message changes.
