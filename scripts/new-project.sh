#!/usr/bin/env bash
# Scaffolds a new project from this template: copies the shared .claude/ setup,
# scripts, CI, and hygiene files into a target directory, then — if the target
# already has a real manifest (package.json, pyproject.toml, go.mod, ...) —
# fingerprints the stack and syncs ci.yml immediately, so a new project never
# ships with CI's generic placeholder left uncustomized (see
# scripts/customize-ci.sh's header for why that matters).
set -euo pipefail

TEMPLATE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:?Usage: scripts/new-project.sh <target-directory>}"

mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"

ITEMS=(
  .claude
  CLAUDE.md
  scripts
  .github
  .devcontainer
  lefthook.yml
  .gitleaks.toml
  commitlint.config.mjs
  .mcp.json
  renovate.json
  .editorconfig
  .semgrep.yml.example
  SECURITY.md
  .gitignore
)

for item in "${ITEMS[@]}"; do
  src="$TEMPLATE_ROOT/$item"
  [[ -e "$src" ]] || continue
  dest="$TARGET/$item"
  if [[ -e "$dest" ]]; then
    echo "Skipping $item — already exists in $TARGET (not overwriting)." >&2
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  cp -R "$src" "$dest"
done

# Generated/local-only state — never copy verbatim from this template's own checkout.
rm -f "$TARGET/.claude/stack-cache.json" "$TARGET/.claude/settings.local.json"
rm -f "$TARGET/.claude/code-graph"/*.json 2>/dev/null || true
rm -rf "$TARGET/.claude/worktrees" 2>/dev/null || true

echo "Copied template scaffolding into $TARGET"

if [[ ! -d "$TARGET/.git" ]]; then
  (cd "$TARGET" && git init -q)
  echo "Initialized a new git repo at $TARGET"
fi

# If a manifest already exists, finish the loop now instead of leaving it as a
# step someone has to remember later.
has_manifest=0
for manifest in package.json pyproject.toml requirements.txt setup.py go.mod Cargo.toml \
                gradlew pom.xml Gemfile Package.swift composer.json; do
  [[ -f "$TARGET/$manifest" ]] && has_manifest=1 && break
done
shopt -s nullglob
tf=("$TARGET"/*.tf) dotnet=("$TARGET"/*.csproj "$TARGET"/*.sln)
[[ ${#tf[@]} -gt 0 || ${#dotnet[@]} -gt 0 ]] && has_manifest=1
shopt -u nullglob

if [[ "$has_manifest" -eq 1 ]]; then
  (cd "$TARGET" && ./scripts/detect-stack.sh && ./scripts/customize-ci.sh)
  echo
  echo "Stack detected and ci.yml synced. Review the diff, then open Claude Code here."
else
  echo
  echo "No manifest found yet — write the project's actual code/manifest first, then run:"
  echo "  cd $TARGET && ./scripts/detect-stack.sh && ./scripts/customize-ci.sh"
fi

if ! command -v lefthook >/dev/null 2>&1; then
  echo
  echo "(optional, recommended) install real git-level secret scanning:"
  echo "  brew install lefthook gitleaks && (cd $TARGET && lefthook install)"
fi
