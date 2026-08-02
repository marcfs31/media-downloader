#!/usr/bin/env bash
# Fingerprints the repo and writes .claude/stack-cache.json describing which
# lint/typecheck/test/build commands to run. Re-run whenever lockfiles/manifests
# change meaningfully. Safe to run repeatedly; it only reads the tree.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
mkdir -p .claude

json() { printf '%s' "$1"; }

emit() {
  local stack="$1" pkgmgr="$2" fmt="$3" lint="$4" typecheck="$5" test="$6" build="$7" licensecheck="${8:-}" vulncheck="${9:-}"
  cat > .claude/stack-cache.json <<EOF
{
  "stack": "$stack",
  "packageManager": "$pkgmgr",
  "format": $(json "\"$fmt\""),
  "lint": $(json "\"$lint\""),
  "typecheck": $(json "\"$typecheck\""),
  "test": $(json "\"$test\""),
  "build": $(json "\"$build\""),
  "licenseCheck": $(json "\"$licensecheck\""),
  "vulnerabilityCheck": $(json "\"$vulncheck\""),
  "generatedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  echo "Detected stack: $stack ($pkgmgr)"
  cat .claude/stack-cache.json
}

# --- This repo's own layout: extension (node) + native-host (python) -------
# A mixed monorepo with no root manifest, so none of the generic single-stack
# branches below can see it. Compound commands run each half from its subdir;
# native-host expects its venv at native-host/.venv (see README > Setup).
if [[ ! -f package.json && -f extension/package.json && -f native-host/pyproject.toml ]]; then
  fmt="(cd extension && pnpm format-check) && (cd native-host && .venv/bin/ruff format --check .)"
  lint="(cd extension && pnpm lint) && (cd native-host && .venv/bin/ruff check .)"
  tc="(cd extension && pnpm typecheck) && (cd native-host && .venv/bin/mypy media_downloader)"
  test="(cd extension && pnpm test) && (cd native-host && .venv/bin/python -m pytest -q)"
  build="(cd extension && pnpm build)"
  # pip-audit stays opt-in (same posture as the generic python branch); pnpm
  # audit is always available wherever pnpm itself is.
  vulncheck="pnpm --dir extension audit --audit-level=high && { [ ! -x native-host/.venv/bin/pip-audit ] || (cd native-host && .venv/bin/pip-audit); }"
  emit "monorepo" "pnpm+pip" "$fmt" "$lint" "$tc" "$test" "$build" "" "$vulncheck"
  exit 0
fi

# --- JS/TS -------------------------------------------------------------
if [[ -f package.json ]]; then
  pm="npm"; run="npm run"
  if [[ -f pnpm-lock.yaml ]]; then pm="pnpm"; run="pnpm"
  elif [[ -f yarn.lock ]]; then pm="yarn"; run="yarn"
  elif [[ -f bun.lockb ]]; then pm="bun"; run="bun run"
  elif [[ ! -f package-lock.json ]]; then pm="pnpm"; run="pnpm"; fi # new project, no lockfile yet: default to pnpm

  has_script() { node -e "process.exit(require('./package.json').scripts && require('./package.json').scripts['$1'] ? 0 : 1)" 2>/dev/null; }

  fmt="$run format --check"; lint="$run lint"; tc="$run typecheck"; test="$run test"; build="$run build"
  has_script format-check && fmt="$run format-check" || true
  has_script format || fmt=""
  has_script lint || lint="npx eslint ."
  has_script typecheck || { [[ -f tsconfig.json ]] && tc="npx tsc --noEmit" || tc=""; }
  has_script test || test="npx vitest run"
  has_script build || build=""

  case "$pm" in
    pnpm) vulncheck="pnpm audit --audit-level=high" ;;
    yarn) vulncheck="yarn audit --level high" ;;
    bun)  vulncheck="bun audit" ;;
    *)    vulncheck="npm audit --audit-level=high" ;;
  esac

  emit "node" "$pm" "$fmt" "$lint" "$tc" "$test" "$build" "license-checker --summary" "$vulncheck"
  exit 0
fi

# --- Python --------------------------------------------------------------
if [[ -f pyproject.toml || -f requirements.txt || -f setup.py ]]; then
  pm="pip"
  if [[ -f uv.lock ]]; then pm="uv"
  elif [[ -f poetry.lock ]]; then pm="poetry"; fi

  case "$pm" in
    uv)     fmt="uv run ruff format --check ."; lint="uv run ruff check ."; tc="uv run mypy ."; test="uv run pytest"; build="" ;;
    poetry) fmt="poetry run ruff format --check ."; lint="poetry run ruff check ."; tc="poetry run mypy ."; test="poetry run pytest"; build="" ;;
    *)      fmt="ruff format --check ."; lint="ruff check ."; tc="mypy ."; test="pytest"; build="" ;;
  esac

  emit "python" "$pm" "$fmt" "$lint" "$tc" "$test" "$build" "pip-licenses --summary" "pip-audit"
  exit 0
fi

# --- Go --------------------------------------------------------------------
if [[ -f go.mod ]]; then
  emit "go" "go" "gofmt -l ." "go vet ./..." "" "go test ./..." "go build ./..." "go-licenses report ./..." "govulncheck ./..."
  exit 0
fi

# --- Rust --------------------------------------------------------------
if [[ -f Cargo.toml ]]; then
  emit "rust" "cargo" "cargo fmt --check" "cargo clippy -- -D warnings" "" "cargo test" "cargo build" "cargo license" "cargo audit"
  exit 0
fi

# --- Java / Kotlin (Gradle or Maven) --------------------------------------
if [[ -f gradlew ]]; then
  emit "jvm" "gradle" "" "./gradlew check" "" "./gradlew test" "./gradlew build"
  exit 0
fi
if [[ -f pom.xml ]]; then
  emit "jvm" "maven" "" "" "" "mvn test" "mvn package"
  exit 0
fi

# --- Ruby --------------------------------------------------------------
if [[ -f Gemfile ]]; then
  emit "ruby" "bundler" "" "bundle exec rubocop" "" "bundle exec rspec" ""
  exit 0
fi

# --- .NET (C#/F#) --------------------------------------------------------
if ls -- *.sln >/dev/null 2>&1 || ls -- *.csproj >/dev/null 2>&1 || ls -- *.fsproj >/dev/null 2>&1; then
  emit "dotnet" "dotnet" "dotnet format --verify-no-changes" "" "" "dotnet test" "dotnet build"
  exit 0
fi

# --- Swift (SwiftPM) ------------------------------------------------------
if [[ -f Package.swift ]]; then
  emit "swift" "swiftpm" "" "" "" "swift test" "swift build"
  exit 0
fi

# --- PHP -------------------------------------------------------------------
if [[ -f composer.json ]]; then
  fmt="vendor/bin/php-cs-fixer fix --dry-run --diff"
  tc="vendor/bin/phpstan analyse"
  test="vendor/bin/phpunit"
  [[ -f vendor/bin/pest ]] && test="vendor/bin/pest"
  emit "php" "composer" "$fmt" "" "$tc" "$test" ""
  exit 0
fi

# --- Terraform / OpenTofu --------------------------------------------------
if ls -- *.tf >/dev/null 2>&1; then
  bin="terraform"
  [[ -f tofu.lock.hcl ]] && bin="tofu"
  # No default "build" step: `plan`/`apply` need real backend credentials and
  # can have side effects, unlike every other stack's build step here — not
  # something to run unattended as part of a correctness gate.
  emit "terraform" "$bin" "$bin fmt -check -recursive" "" "$bin validate" "" ""
  exit 0
fi

echo "No recognized manifest found at repo root. Writing an empty stack cache." >&2
emit "unknown" "" "" "" "" "" ""
