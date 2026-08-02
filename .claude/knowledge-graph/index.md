---
name: index
type: module
related: []
---

Entry point for this repo's knowledge graph. Add one line per entry below as they're
created under `decisions/`, `modules/`, or `footguns/`, newest-relevant first. See
`.claude/skills/knowledge-graph/SKILL.md` for the format and when to update this.

The entries below are a worked example — real decisions/facts about *this template
repo itself* — so a new project copied from this template starts from a populated,
correctly-formatted graph instead of an empty folder with only the SKILL.md
description to go on. Delete them once your own project has real entries to replace
them with; don't leave them as stale placeholders describing a different codebase.

## Entries

- [vulnerability-check-gates-license-check-doesnt](decisions/vulnerability-check-gates-license-check-doesnt.md) — why one optional dependency-scan step fails the gate and the other doesn't
- [layered-secret-scanning](decisions/layered-secret-scanning.md) — why gitleaks runs in three separate places instead of one
- [conventional-commits-adopted](decisions/conventional-commits-adopted.md) — commitlint enforces the type-prefix convention, with subject-case relaxed to match existing history
- [correctness-gate-script](modules/correctness-gate-script.md) — what `run-checks.sh` does and why it must never diverge from CI
- [stack-cache-staleness](footguns/stack-cache-staleness.md) — `.claude/stack-cache.json` doesn't auto-refresh; re-run `detect-stack.sh` after a stack change
