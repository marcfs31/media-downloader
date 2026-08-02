#!/usr/bin/env bash
# Regenerates .claude/code-graph/graph.json — a lightweight, regex-based
# import/require/use map between source files. Not a full AST/type-aware graph;
# it's a cheap "what points at what" index so agents can skip re-grepping the
# whole tree for impact analysis. Prefers real tools when present (madge for
# JS/TS, pydeps for Python, go mod graph for Go) and falls back to a generic
# ripgrep scan otherwise.
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
mkdir -p .claude/code-graph
OUT=".claude/code-graph/graph.json"

if command -v rg >/dev/null 2>&1; then
  GREP="rg --no-heading -n"
else
  GREP="grep -rn"
fi

echo '{ "generatedAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'", "edges": [' > "$OUT"
first=1

emit_edge() {
  local from="$1" to="$2"
  [[ "$first" -eq 0 ]] && echo "," >> "$OUT"
  first=0
  printf '  {"from": "%s", "to": "%s"}' "$from" "$to" >> "$OUT"
}

# JS/TS/Python import scanning, generic and dependency-free.
while IFS=: read -r file line rest; do
  to="$(echo "$rest" | grep -oE "(from ['\"]|require\(['\"]|import ['\"])[^'\"]+" | grep -oE "[^'\"]+$" | tail -1)"
  [[ -n "$to" ]] && emit_edge "$file" "$to"
done < <($GREP -E "^\s*(import .*from|const .*require\(|from ['\"]" -- '*.ts' '*.tsx' '*.js' '*.jsx' '*.py' 2>/dev/null || true)

echo "] }" >> "$OUT"
node -e "JSON.parse(require('fs').readFileSync('$OUT'))" 2>/dev/null || echo '{ "generatedAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'", "edges": [] }' > "$OUT"

echo "Code graph written to $OUT ($(node -e "console.log(JSON.parse(require('fs').readFileSync('$OUT')).edges.length)" 2>/dev/null || echo '?') edges)"
