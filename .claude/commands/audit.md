---
description: Spawn the sandbox-auditor agent in an isolated worktree to review everything changed since the last audit
---

Spawn the `sandbox-auditor` subagent (`.claude/agents/sandbox-auditor.md`) with
`isolation: "worktree"`, running in the background unless the user asked to wait
for the result. Don't pre-summarize what it will find — the agent determines its
own scope (via the `sandbox-audit/last` git tag, per its own instructions) and
reports back independently.

See `.claude/skills/continuous-review/SKILL.md` for when this is the right tool
versus the correctness gate or `/code-review`, and for how to wrap this in `/loop`
for recurring checks during a long session.
