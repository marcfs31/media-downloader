---
name: stack-cache-staleness
type: footgun
related: [correctness-gate-script]
---

`.claude/stack-cache.json` is a generated cache of the detected stack (package
manager, format/lint/typecheck/build/test commands) — nothing re-detects it
automatically when the project changes. Adding a second language, switching package
managers, or adding a new lockfile silently leaves `run-checks.sh` running the old
stack's commands (or none, if the cache still says `"stack": "unknown"`) until
someone re-runs `./scripts/detect-stack.sh`. Treat a gate that looks like it's
skipping checks it should be running as a sign to re-run detection, not a sign the
gate script is broken.
