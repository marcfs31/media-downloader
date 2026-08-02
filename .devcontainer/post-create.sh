#!/usr/bin/env bash
# Runs once after the devcontainer is created. Best-effort: a failure here
# shouldn't block the container from coming up, so nothing in this script exits
# non-zero on a step that doesn't apply to a given project.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v brew >/dev/null 2>&1; then
  brew install lefthook gitleaks 2>&1 | tail -5 || echo "brew install lefthook/gitleaks failed — install manually, see README"
else
  echo "Homebrew not found in this image — install lefthook/gitleaks manually, see README"
fi

if [[ -f package.json ]] && command -v corepack >/dev/null 2>&1; then
  corepack enable 2>/dev/null || true
fi

if [[ -x ./scripts/detect-stack.sh ]]; then
  ./scripts/detect-stack.sh || true
fi

if [[ -f lefthook.yml ]] && command -v lefthook >/dev/null 2>&1; then
  lefthook install || true
fi

echo "Devcontainer setup done."
