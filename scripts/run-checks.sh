#!/usr/bin/env bash
# The single correctness gate: format-check, lint, typecheck, build, test.
# Used by the Stop hook locally AND by CI (.github/workflows/ci.yml) — keep them
# calling this script, not duplicating the commands, so they can never drift apart.
#
# Exit code 0 = clean. Non-zero = something failed; prints which step(s).
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

if [[ ! -f .claude/stack-cache.json ]]; then
  ./scripts/detect-stack.sh >/dev/null
fi

get() { node -e "console.log(JSON.parse(require('fs').readFileSync('.claude/stack-cache.json')).$1 || '')" 2>/dev/null || \
        python3 -c "import json;print(json.load(open('.claude/stack-cache.json')).get('$1',''))" 2>/dev/null; }

STACK="$(get stack)"
FMT="$(get format)"
LINT="$(get lint)"
TC="$(get typecheck)"
TEST="$(get test)"
BUILD="$(get build)"
LICENSE_CHECK="$(get licenseCheck)"
VULNERABILITY_CHECK="$(get vulnerabilityCheck)"

failed=0
run_step() {
  local label="$1" cmd="$2"
  [[ -z "$cmd" ]] && return 0
  echo "--- $label: $cmd"
  if ! eval "$cmd"; then
    echo "!!! $label FAILED"
    failed=1
  fi
}

# Cargo subcommands (e.g. `cargo license`, `cargo audit`) install as
# `cargo-<name>` on PATH — resolve to the real binary to check for, not the
# `cargo` wrapper every Rust project already has.
resolve_check_binary() {
  local cmd="$1"
  case "$cmd" in
    "cargo "*) local bin="cargo-${cmd#"cargo "}"; echo "${bin%% *}" ;;
    *)         echo "${cmd%% *}" ;;
  esac
}

if [[ "$STACK" == "unknown" || -z "$STACK" ]]; then
  echo "No known stack detected — skipping stack-specific checks (edit CLAUDE.md's Stack section if this is wrong)."
else
  echo "Running correctness gate for stack: $STACK"
  run_step "format" "$FMT"
  run_step "lint" "$LINT"
  run_step "typecheck" "$TC"
  run_step "build" "$BUILD"
  run_step "test" "$TEST"
fi

# --- Optional deeper checks -------------------------------------------------
# Stack-independent, auto-detected: run only if the tool is installed (and, for
# tools that need project-specific config to be meaningful, only if that config
# exists). Zero cost if absent — this is the same "detect, don't assume"
# philosophy as the stack detection above. See README > Recommended external
# tools for what each of these is and how to install it.

if command -v gitleaks >/dev/null 2>&1; then
  run_step "gitleaks (secret scan)" "gitleaks detect --no-banner --redact -v"
fi

if command -v semgrep >/dev/null 2>&1 && [[ -f .semgrep.yml || -d .semgrep ]]; then
  run_step "semgrep (security/correctness)" "semgrep --config .semgrep.yml --error --quiet"
fi

if command -v ast-grep >/dev/null 2>&1 && [[ -f .claude/ast-grep/sgconfig.yml ]] \
   && find .claude/ast-grep/rules -name '*.yml' -o -name '*.yaml' 2>/dev/null | grep -q .; then
  run_step "ast-grep (structural lint)" "ast-grep scan --config .claude/ast-grep/sgconfig.yml"
fi

if command -v actionlint >/dev/null 2>&1 && ls .github/workflows/*.y*ml >/dev/null 2>&1; then
  run_step "actionlint (CI workflow lint)" "actionlint"
fi

if command -v bats >/dev/null 2>&1 && [[ -d scripts/tests ]]; then
  run_step "bats (scripts/ meta-tests)" "bats scripts/tests"
fi

if [[ -n "$LICENSE_CHECK" ]] && command -v "$(resolve_check_binary "$LICENSE_CHECK")" >/dev/null 2>&1; then
  # Informational by default (no policy flags), so it never fails the gate on
  # its own — add an allow/deny flag to licenseCheck in stack-cache.json (e.g.
  # license-checker's --onlyAllow, pip-licenses's --allow-only) once this
  # project has an actual license policy to enforce.
  run_step "license check (informational)" "$LICENSE_CHECK"
fi

if [[ -n "$VULNERABILITY_CHECK" ]] \
   && command -v "$(resolve_check_binary "$VULNERABILITY_CHECK")" >/dev/null 2>&1; then
  # Unlike the license check, this DOES fail the gate — a known CVE in a
  # dependency isn't a matter of project-specific policy the way license
  # approval is. For npm/pnpm/yarn/bun this is the package manager's own
  # audit subcommand (always present wherever fmt/lint/test already run); for
  # other stacks it's a separate opt-in tool, same detect-and-use posture as
  # everything else in this section.
  run_step "vulnerability check" "$VULNERABILITY_CHECK"
fi

if [[ "$failed" -eq 0 ]]; then
  echo "Correctness gate: clean."
else
  echo "Correctness gate: FAILED — see steps marked above."
fi
exit "$failed"
