# Project Instructions

This repo was bootstrapped from the Claude Code starter template. This file is the
first thing Claude reads — keep it accurate as the project evolves; it's cheaper to
fix a stale line here than to re-explain the same thing every session.

## Stack

**This repo is now the Media Downloader project** — a mixed monorepo with two parts:

- **`extension/`** — TypeScript browser extension (Chrome MV3 + Firefox), pnpm,
  esbuild (`pnpm build` → `dist/chrome` and `dist/firefox`), Vitest (jsdom),
  ESLint + Prettier, `tsc --noEmit`. Pure detection logic lives in
  `src/media-detect.ts` so it stays testable without extension APIs.
- **`native-host/`** — Python native-messaging host + CLI wrapping yt-dlp and
  requests. Tooling lives in `native-host/.venv` (one-time setup:
  `python3 -m venv .venv && .venv/bin/pip install -e . --group dev` from
  `native-host/`), ruff (lint + format), mypy `--strict`, pytest.

`scripts/detect-stack.sh` has a dedicated branch for this layout (stack
`"monorepo"`) that emits compound per-subdir commands, so the correctness gate
and CI cover both halves — don't hand-edit `.claude/stack-cache.json`, re-run
the script. The shared message protocol between extension contexts is
`extension/src/shared.ts`; the native messaging host name
(`com.media_downloader.host`) must match between `shared.ts` and
`native-host/media_downloader/install.py`.

Don't assume a toolchain — the repo tells you. Run `./scripts/detect-stack.sh` if
`.claude/stack-cache.json` looks missing or stale (new lockfile, new manifest, etc.);
it's cheap and regenerates the cache other tooling reads from.

Defaults if a project has no stronger opinion of its own:
- **JS/TS**: pnpm, Vitest for unit tests, Playwright for e2e, ESLint + Prettier, `tsc --noEmit` for type checking.
- **Python**: uv (fallback: poetry, then pip), pytest, ruff (lint + format), mypy.
- **Go**: `go build`, `go vet`, `go test ./...`.
- **Rust**: `cargo build`, `cargo clippy`, `cargo test`.
- **Java/Kotlin**: Gradle or Maven (whichever wrapper is present), their standard test task.
- **Ruby**: Bundler, RSpec, RuboCop.
- **.NET (C#/F#)**: `dotnet format --verify-no-changes`, `dotnet build`, `dotnet test`.
- **Swift**: SwiftPM (`swift build`, `swift test`).
- **PHP**: Composer, PHPUnit (or Pest if present), PHPStan, php-cs-fixer.
- **Terraform/OpenTofu**: `fmt -check -recursive`, `validate` — deliberately no default
  build/plan step, since `plan`/`apply` need real backend credentials and can have
  side effects, unlike every other stack's build step.

If the actual project deviates (custom scripts, monorepo with mixed stacks, a
non-standard test command), **edit this section** rather than letting Claude
rediscover it every session.

## Correctness gate

`scripts/run-checks.sh` is the single source of truth for "is this change correct."
It runs format-check, lint, typecheck, build, and test for whatever
`detect-stack.sh` found, and the same script runs in CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)) — never diverge these.

- A `PostToolUse` hook auto-formats any file Claude edits (fast, silent, no confirmation needed).
- A `Stop` hook runs the full gate before treating a turn as "done" and reports failures.
- Before saying a task is complete, the gate must be clean. If it can't pass (flaky
  test, pre-existing failure unrelated to the change), say so explicitly rather than
  reporting success.
- For anything non-trivial, prefer the `engineering:code-review` skill (or
  `/code-review ultra` for a full multi-agent pass) over eyeballing a diff before
  calling it done.
- The gate also runs deeper, opt-in static analysis when the tools are installed:
  gitleaks (secrets), semgrep (only if `.semgrep.yml` exists — see
  `.semgrep.yml.example`), ast-grep (only if `.claude/ast-grep/rules/` has an active
  rule), actionlint (CI workflow files), a dependency license report matching the
  detected stack (informational only until a policy flag is added — see README >
  Recommended external tools), a dependency vulnerability scan matching the
  detected stack (`npm`/`pnpm`/`yarn`/`bun audit` for Node — no extra install,
  since the package manager is already required; `pip-audit`/`govulncheck`/
  `cargo-audit` elsewhere — this one **does** fail the gate, since a known CVE
  isn't policy the way license approval is), and — if [bats-core](https://github.com/bats-core/bats-core)
  is installed — `scripts/tests/*.bats`, a regression suite for `detect-stack.sh`/
  `run-checks.sh`/`checkpoint.sh` themselves; also gate-failing. None of these
  block a fresh checkout that hasn't installed them — see README > Recommended
  external tools.
- This repo's `.mcp.json` wires in [context7](https://github.com/upstash/context7)
  for live library docs — prefer it over guessing an API's shape from training data
  when working with an unfamiliar or fast-moving dependency.

## Git checkpoint policy

This project has opted into **automatic local checkpoint commits**: the `Stop` hook
(`scripts/checkpoint.sh`) commits WIP automatically when the correctness gate is clean,
but only when:
- the current branch is **not** `main`/`master`/`trunk`/`develop`, and
- there's something to commit, and
- no file that looks like a secret (`.env*`, `*.pem`, `*.key`, `credentials*.json`, etc.) is staged — those are skipped and flagged instead, and
- if [gitleaks](https://github.com/gitleaks/gitleaks) is installed, it content-scans the staged diff and blocks the commit outright on a hit — the filename check alone can't catch a hardcoded key inside a normal source file.

If [lefthook](https://github.com/evilmartians/lefthook) is installed (`lefthook install`),
the same gitleaks scan also runs as a real git `pre-commit` hook (`lefthook.yml`) —
this is the layer that protects a manual `git commit` made outside a Claude Code
session too.

This is a durable, standing authorization for **local, feature-branch commits only**.
It does **not** authorize:
- pushing to any remote,
- committing on a protected branch,
- amending or rewriting existing history,
- force-anything.

Those still require explicit confirmation every time, per the normal safety rules. If
you don't want auto-checkpointing on a given project, delete the `Stop` hook entry in
`.claude/settings.json` or point `scripts/checkpoint.sh`'s branch check at `*` to
disable it everywhere.

## Swarming agents

Don't default to doing everything serially in the main thread. See
[.claude/skills/swarm/SKILL.md](.claude/skills/swarm/SKILL.md) and use `/swarm` when a
task is genuinely parallelizable:
- broad codebase research → parallel `Explore` agents over different subsystems,
- multi-angle review of a diff → parallel reviewer subagents (correctness / security /
  simplicity), synthesized into one findings list,
- independent features/fixes → parallel `implementer` agents in isolated git worktrees,
- a hard problem with more than one plausible approach → competitive/ensemble agents,
  compare, pick or merge the best.

Don't spawn a swarm for something one agent can just do — the coordination overhead
isn't free.

## Continuous review

For a long autonomous session producing a lot of code, `/audit` (see
[.claude/skills/continuous-review/SKILL.md](.claude/skills/continuous-review/SKILL.md))
spawns `sandbox-auditor` — an independent agent in its own isolated worktree that
audits everything changed since the last audit (tracked via a local-only
`sandbox-audit/last` git tag, not conversation memory) and reports back without
ever modifying the main working tree. Wrap it in `/loop 15m /audit` for recurring
checks during a session, rather than only reviewing at the end. This complements,
not replaces, the correctness gate and `/code-review`.

## Code-structure and knowledge graphs

Two graphs live in this repo instead of being rebuilt from scratch every session:

- **`.claude/code-graph/`** — generated. Import/call relationships between files, kept
  current by `scripts/build-code-graph.sh` (invoked by the `graph-keeper` subagent or
  `/graph-update`). Treat it as a cache: regenerate, don't hand-edit.
- **`.claude/knowledge-graph/`** — curated. Why things are the way they are: key
  decisions, module ownership/purpose, external service dependencies, known footguns.
  Hand-edited and agent-maintained; update it whenever a change reveals something a
  future session would otherwise have to rediscover the hard way.

Consult both before a broad exploration pass — they're usually faster and more
reliable than re-grepping the whole tree. `.claude/ast-grep/` is a related but
distinct tool: structural pattern matching (tree-sitter based) for catching a
specific kind of mistake repeatedly, rather than mapping relationships — see
`.claude/skills/code-graph/SKILL.md`.

## GitHub conventions

GitHub is the system of record for this project (PRs, issues, Actions).
- Branch names: `<type>/<short-description>` (`fix/`, `feat/`, `chore/`, `refactor/`).
- Commit messages: Conventional Commits type prefix matching the branch-type
  vocabulary above (`feat:`, `fix:`, `chore:`, `refactor:`, plus the standard
  `docs:`/`test:`/`ci:`/`build:`/`perf:`/`style:`/`revert:` as needed), imperative
  mood, why over what, no trailing period on the summary line. Enforced by
  commitlint (`commitlint.config.mjs`) via the `commit-msg` hook in
  [lefthook.yml](lefthook.yml) once a project opts in with
  `pnpm add -D @commitlint/cli @commitlint/config-conventional && lefthook install`
  — inert (skips silently) until then, same opt-in posture as the gitleaks
  pre-commit hook.
- Open PRs via `gh pr create`; the description should satisfy
  `.github/pull_request_template.md`. Let CI (`.github/workflows/ci.yml`) run the
  same gate as local hooks before requesting review.
- Never push or merge without explicit confirmation, regardless of how clean the gate
  is.
- Dependency updates arrive as Renovate PRs (`renovate.json`) once the Renovate app
  is installed on the repo — review like any other PR, don't auto-merge major
  version bumps.

## Style

- No comments unless they capture a non-obvious *why*.
- No speculative abstraction — solve the task in front of you.
- Prefer editing existing files over creating new ones; don't create docs/plans unless
  asked.
- No dead code, and no hardcoded stand-ins for something that's supposed to be
  configurable or user-controlled (a value frozen to its default with no UI/API path
  to change it, a flag that's always the same because nothing ever sets it
  otherwise). If a schema/type says a field is meant to vary, either wire up the real
  path to set it or don't add the field yet — a field that silently can never be
  anything but its default is a bug wearing a schema's clothes, not a checked box.
  Same for unreachable branches, unused exports, and TODO-and-abandon stubs: finish
  it, remove it, or if it's deliberately out of scope, say so explicitly rather than
  leaving it half-wired.
