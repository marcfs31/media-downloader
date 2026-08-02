---
description: Refresh the code-structure graph and update the knowledge graph based on recent changes
---

Invoke the `graph-keeper` subagent (`.claude/agents/graph-keeper.md`) to:

1. Regenerate `.claude/code-graph/graph.json` via `./scripts/build-code-graph.sh`.
2. Review recent changes (`git diff` against the base branch, or the current
   session's edits if this isn't a git-tracked change yet) and update
   `.claude/knowledge-graph/` with anything non-obvious a future session would
   otherwise have to rediscover — new modules, decisions, footguns, external
   dependencies.

Report back a short summary: what changed in each graph, and nothing more.
