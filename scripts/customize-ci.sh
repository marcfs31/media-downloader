#!/usr/bin/env bash
# Fills in .github/workflows/ci.yml's runtime-setup step(s) based on
# .claude/stack-cache.json, so CI never ships with the template's generic
# placeholder left uncustomized (a real bug the sandbox-auditor caught once —
# see .claude/knowledge-graph/ if this repo has an entry for it).
#
# Replaces everything between the "BEGIN CUSTOMIZE-CI"/"END CUSTOMIZE-CI"
# markers in ci.yml. Safe to re-run any time the stack changes; safe to
# hand-edit the result afterward — only a future run of this script touches
# that block again.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

CI_FILE=".github/workflows/ci.yml"
CACHE=".claude/stack-cache.json"

if [[ ! -f "$CI_FILE" ]]; then
  echo "No $CI_FILE — nothing to customize." >&2
  exit 0
fi

if [[ ! -f "$CACHE" ]]; then
  echo "No $CACHE — run scripts/detect-stack.sh first." >&2
  exit 1
fi

get() { node -e "console.log(JSON.parse(require('fs').readFileSync('$CACHE')).$1 || '')" 2>/dev/null || \
        python3 -c "import json;print(json.load(open('$CACHE')).get('$1',''))" 2>/dev/null; }

STACK="$(get stack)"
PM="$(get packageManager)"

steps=""
add() { steps="$steps      $1"$'\n'; }

case "$STACK" in
  monorepo)
    add "- uses: pnpm/action-setup@v4"
    add "  with: { package_json_file: extension/package.json }"
    add "- uses: actions/setup-node@v4"
    add "  with: { node-version: 22, cache: pnpm, cache-dependency-path: extension/pnpm-lock.yaml }"
    add "- run: pnpm --dir extension install --frozen-lockfile"
    add "- uses: actions/setup-python@v5"
    add "  with: { python-version: '3.12' }"
    add "- run: python3 -m venv native-host/.venv && cd native-host && .venv/bin/pip install -e . --group dev"
    ;;
  node)
    case "$PM" in
      pnpm)
        add "- uses: pnpm/action-setup@v4"
        add "- uses: actions/setup-node@v4"
        add "  with: { node-version: 20, cache: pnpm }"
        add "- run: pnpm install --frozen-lockfile"
        ;;
      yarn)
        add "- uses: actions/setup-node@v4"
        add "  with: { node-version: 20, cache: yarn }"
        add "- run: yarn install --frozen-lockfile"
        ;;
      bun)
        add "- uses: oven-sh/setup-bun@v2"
        add "- run: bun install --frozen-lockfile"
        ;;
      *)
        add "- uses: actions/setup-node@v4"
        add "  with: { node-version: 20, cache: npm }"
        add "- run: npm ci"
        ;;
    esac
    ;;
  python)
    case "$PM" in
      uv)
        add "- uses: astral-sh/setup-uv@v3"
        add "- run: uv sync --all-extras"
        ;;
      poetry)
        add "- uses: actions/setup-python@v5"
        add "  with: { python-version: '3.12' }"
        add "- uses: snok/install-poetry@v1"
        add "- run: poetry install"
        ;;
      *)
        add "- uses: actions/setup-python@v5"
        add "  with: { python-version: '3.12' }"
        add "- run: pip install -r requirements.txt"
        ;;
    esac
    ;;
  go)
    add "- uses: actions/setup-go@v5"
    add "  with: { go-version: stable }"
    ;;
  rust)
    add "- uses: dtolnay/rust-toolchain@stable"
    ;;
  jvm)
    add "- uses: actions/setup-java@v4"
    add "  with: { distribution: temurin, java-version: '21' }"
    ;;
  ruby)
    add "- uses: ruby/setup-ruby@v1"
    add "  with: { bundler-cache: true }"
    ;;
  dotnet)
    add "- uses: actions/setup-dotnet@v4"
    add "  with: { dotnet-version: '8.0.x' }"
    add "- run: dotnet restore"
    ;;
  swift)
    add "- uses: swift-actions/setup-swift@v2"
    ;;
  php)
    add "- uses: shivammathur/setup-php@v2"
    add "  with: { php-version: '8.3' }"
    add "- run: composer install --prefer-dist --no-progress"
    ;;
  terraform)
    add "- uses: hashicorp/setup-terraform@v3"
    [[ "$PM" == "tofu" ]] && add "  with: { terraform_wrapper: false }"
    add "- run: $PM init -backend=false"
    ;;
  *)
    echo "Unrecognized/unknown stack ('$STACK') — leaving ci.yml's placeholder as-is." >&2
    exit 0
    ;;
esac

python3 - "$CI_FILE" "$steps" <<'PYEOF'
import re, sys
path, steps = sys.argv[1], sys.argv[2]
text = open(path).read()
pattern = re.compile(
    r"(      # BEGIN CUSTOMIZE-CI.*?\n)(.*?)(      # END CUSTOMIZE-CI\n)",
    re.DOTALL,
)
if not pattern.search(text):
    print(f"No BEGIN/END CUSTOMIZE-CI markers found in {path} — leaving it untouched.", file=sys.stderr)
    sys.exit(1)
text = pattern.sub(lambda m: m.group(1) + steps + m.group(3), text, count=1)
open(path, "w").write(text)
PYEOF

echo "Updated $CI_FILE for stack: $STACK${PM:+ ($PM)}"
