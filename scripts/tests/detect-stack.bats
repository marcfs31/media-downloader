setup() {
  load helpers
  setup_fake_repo
}

@test "detects node stack from package.json + pnpm-lock.yaml" {
  echo '{"name":"x"}' > package.json
  touch pnpm-lock.yaml
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "node"' .claude/stack-cache.json
  grep -q '"packageManager": "pnpm"' .claude/stack-cache.json
}

@test "defaults node package manager to npm when only package-lock.json is present" {
  echo '{"name":"x"}' > package.json
  touch package-lock.json
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"packageManager": "npm"' .claude/stack-cache.json
}

@test "detects python stack from pyproject.toml + uv.lock" {
  echo '[project]' > pyproject.toml
  touch uv.lock
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "python"' .claude/stack-cache.json
  grep -q '"packageManager": "uv"' .claude/stack-cache.json
}

@test "detects go stack from go.mod" {
  echo 'module example.com/x' > go.mod
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "go"' .claude/stack-cache.json
  grep -q '"licenseCheck": "go-licenses report ./..."' .claude/stack-cache.json
  grep -q '"vulnerabilityCheck": "govulncheck ./..."' .claude/stack-cache.json
}

@test "detects rust stack from Cargo.toml" {
  echo '[package]' > Cargo.toml
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "rust"' .claude/stack-cache.json
  grep -q '"licenseCheck": "cargo license"' .claude/stack-cache.json
  grep -q '"vulnerabilityCheck": "cargo audit"' .claude/stack-cache.json
}

@test "node vulnerability check uses the detected package manager's own audit subcommand" {
  echo '{"name":"x"}' > package.json
  touch pnpm-lock.yaml
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"vulnerabilityCheck": "pnpm audit --audit-level=high"' .claude/stack-cache.json
}

@test "python vulnerability check is pip-audit" {
  echo '[project]' > pyproject.toml
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"vulnerabilityCheck": "pip-audit"' .claude/stack-cache.json
}

@test "falls back to unknown with no manifest" {
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "unknown"' .claude/stack-cache.json
}

@test "node detection prefers a more specific lockfile over an already-set default" {
  echo '{"name":"x"}' > package.json
  touch yarn.lock
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"packageManager": "yarn"' .claude/stack-cache.json
}

@test "is safe to run twice in a row" {
  echo '{"name":"x"}' > package.json
  ./scripts/detect-stack.sh >/dev/null
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "node"' .claude/stack-cache.json
}

@test "detects the extension+native-host mixed monorepo layout" {
  mkdir -p extension native-host
  echo '{"name":"x"}' > extension/package.json
  echo '[project]' > native-host/pyproject.toml
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "monorepo"' .claude/stack-cache.json
  grep -q '"packageManager": "pnpm+pip"' .claude/stack-cache.json
}

@test "a root manifest wins over the monorepo layout" {
  mkdir -p extension native-host
  echo '{"name":"x"}' > extension/package.json
  echo '[project]' > native-host/pyproject.toml
  echo '{"name":"root"}' > package.json
  run ./scripts/detect-stack.sh
  [ "$status" -eq 0 ]
  grep -q '"stack": "node"' .claude/stack-cache.json
}
