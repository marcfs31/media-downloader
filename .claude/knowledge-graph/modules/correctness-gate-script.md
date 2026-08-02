---
name: correctness-gate-script
type: module
related: [layered-secret-scanning]
---

`scripts/run-checks.sh` is the single source of truth for "is this change correct" —
it reads `.claude/stack-cache.json` (written by `scripts/detect-stack.sh`) to find
the right format/lint/typecheck/build/test commands for whatever stack is actually
present, then runs them plus optional deeper analysis (gitleaks, semgrep if
`.semgrep.yml` exists, ast-grep if `.claude/ast-grep/rules/` has an active rule,
actionlint). The exact same script runs locally (via the `Stop` hook) and in CI
(`.github/workflows/ci.yml`) — the two must never diverge, so a fix belongs in this
script, never duplicated into the workflow file directly. If it reports "No known
stack detected," `.claude/stack-cache.json` is stale or missing; re-run
`detect-stack.sh` rather than editing the cache by hand.
