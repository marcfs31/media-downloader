---
description: Fan out a task across parallel agents using the swarm patterns in .claude/skills/swarm/SKILL.md
argument-hint: <task description>
---

Read `.claude/skills/swarm/SKILL.md` and decide which pattern fits: research,
review, worktree implementation, or ensemble/competitive.

Task: $ARGUMENTS

1. State which pattern you're using and why, in one sentence.
2. Break the task into genuinely independent units — if you can't find independent
   units, say so and do it as one agent instead of forcing a swarm.
3. Batch the `Agent` calls for those units into a single message so they run in
   parallel (background unless the result is needed immediately to proceed).
4. When they report back, synthesize — don't just concatenate their outputs. Dedupe
   overlapping findings, resolve conflicting recommendations, and give one coherent
   result.
