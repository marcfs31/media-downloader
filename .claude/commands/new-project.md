---
description: Scaffold a new project from this template into a target directory
argument-hint: <target-directory>
---

Run `./scripts/new-project.sh $ARGUMENTS`.

This copies the shared `.claude/` setup, `scripts/`, CI, and hygiene files into the
target, `git init`s it if it isn't already a repo, and — if the target already has a
real manifest (`package.json`, `pyproject.toml`, `go.mod`, etc.) — fingerprints the
stack and syncs `.github/workflows/ci.yml`'s runtime-setup step immediately, so CI
never ships with the generic placeholder left uncustomized.

If the target has no manifest yet, the script prints the two-command follow-up
(`detect-stack.sh && customize-ci.sh`) to run once the project's actual code exists —
report that follow-up back clearly rather than silently leaving it for later.
