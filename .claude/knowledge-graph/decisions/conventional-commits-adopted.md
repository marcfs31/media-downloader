---
name: conventional-commits-adopted
type: decision
related: [layered-secret-scanning]
---

Commit messages require a Conventional Commits type prefix (`feat:`, `fix:`,
`chore:`, `refactor:`, plus the standard extras), enforced by `commitlint.config.mjs`
via `lefthook.yml`'s `commit-msg` hook — matching the type vocabulary already used
for branch names (`<type>/<short-description>`). `subject-case` is overridden to
only forbid all-caps subjects, not sentence-case, because this repo's established
style capitalizes the first word ("Add PWA installability", not lowercase) and the
default `@commitlint/config-conventional` would otherwise reject every commit
already in this repo's history. Same opt-in posture as gitleaks: inert until a
project installs `@commitlint/cli` + `@commitlint/config-conventional` and runs
`lefthook install`.
