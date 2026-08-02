---
name: layered-secret-scanning
type: decision
related: [correctness-gate-script, conventional-commits-adopted]
---

gitleaks runs in three separate places — `scripts/checkpoint.sh` (the Claude Code
`Stop` hook), `lefthook.yml`'s `pre-commit` hook (a real git hook), and
`scripts/run-checks.sh` (the gate, also run in CI) — instead of once. Each covers a
gap the others can't: the `Stop` hook only fires inside a Claude Code session and
only scans staged content at commit time; the git hook fires for *any* commit
(a human, a different tool, a script) even outside Claude Code entirely; the gate/CI
copy is the final backstop that catches anything already merged. All three are inert
until the `gitleaks` binary is installed — see README > Recommended external tools —
so a fresh checkout is never blocked by a missing dependency, but also never
silently unprotected once it's installed once.
