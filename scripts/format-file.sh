#!/usr/bin/env bash
# PostToolUse hook target: formats whichever file was just edited/written.
# Reads the hook JSON payload from stdin (Claude Code hook convention) and
# formats only that file — fast enough to run with no confirmation on every edit.
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

payload="$(cat)"
file_path="$(node -e "
  try {
    const p = JSON.parse(process.argv[1]);
    console.log(p.tool_input && (p.tool_input.file_path || p.tool_input.path) || '');
  } catch (e) { console.log(''); }
" "$payload" 2>/dev/null)"

[[ -z "$file_path" || ! -f "$file_path" ]] && exit 0

case "$file_path" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.json|*.css|*.md)
    if [[ -f node_modules/.bin/prettier ]]; then node_modules/.bin/prettier --write "$file_path" >/dev/null 2>&1; fi ;;
  *.py)
    if command -v ruff >/dev/null 2>&1; then ruff format "$file_path" >/dev/null 2>&1; fi ;;
  *.go)
    command -v gofmt >/dev/null 2>&1 && gofmt -w "$file_path" ;;
  *.rs)
    command -v rustfmt >/dev/null 2>&1 && rustfmt "$file_path" >/dev/null 2>&1 ;;
  *.rb)
    if command -v bundle >/dev/null 2>&1; then bundle exec rubocop -A "$file_path" >/dev/null 2>&1; fi ;;
esac

exit 0
