#!/usr/bin/env bash
# Stop hook target: runs the correctness gate, and if clean, auto-commits a local
# WIP checkpoint — but only on a feature branch, never on main/master/trunk/develop,
# and never if it would stage anything that looks like a secret. This never pushes.
#
# Secret protection is two-layered: a filename heuristic always runs (zero
# dependency), and if gitleaks (https://github.com/gitleaks/gitleaks) is installed,
# it also content-scans staged changes and aborts the commit on a hit — the filename
# check alone can't catch a hardcoded key inside an otherwise ordinary source file.
#
# See CLAUDE.md > "Git checkpoint policy" for the authorization this relies on.
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
case "$branch" in
  main|master|trunk|develop|"")
    exit 0 ;;
esac

./scripts/run-checks.sh
gate_status=$?
if [[ "$gate_status" -ne 0 ]]; then
  echo "Checkpoint skipped: correctness gate is not clean."
  exit 0
fi

git add -A -N >/dev/null 2>&1 # dry-add to list new files without staging content yet
changed="$(git status --porcelain | awk '{print $2}')"
[[ -z "$changed" ]] && { echo "Checkpoint skipped: nothing to commit."; exit 0; }

secret_pattern='(^|/)(\.env(\..*)?|.*\.pem|.*\.key|credentials.*\.json|.*_secret.*|id_rsa.*)$'
safe_files=()
skipped_files=()
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  if [[ "$f" =~ $secret_pattern ]]; then
    skipped_files+=("$f")
  else
    safe_files+=("$f")
  fi
done <<< "$changed"

git reset >/dev/null 2>&1 # undo the dry-add before staging for real

if [[ "${#skipped_files[@]}" -gt 0 ]]; then
  printf 'Checkpoint: skipping likely-secret file(s), stage/commit manually if intentional:\n'
  printf '  %s\n' "${skipped_files[@]}"
fi

[[ "${#safe_files[@]}" -eq 0 ]] && { echo "Checkpoint skipped: only secret-like files changed."; exit 0; }

git add -- "${safe_files[@]}"

if command -v gitleaks >/dev/null 2>&1; then
  if ! gitleaks protect --staged --no-banner --redact 2>&1; then
    git reset >/dev/null 2>&1
    echo "Checkpoint blocked: gitleaks found a likely secret in staged content above."
    echo "Fix it, or if it's a false positive, add an allowlist entry in .gitleaks.toml."
    exit 1
  fi
else
  echo "Checkpoint: gitleaks not installed — only the filename heuristic ran. See README > Recommended external tools."
fi

git commit -q -m "chore: checkpoint (auto, gate clean) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Checkpoint committed on '$branch' (${#safe_files[@]} file(s)). Push still requires explicit confirmation."
