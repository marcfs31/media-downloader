# Shared setup for the scripts/ meta-test suite. Each test runs against an
# isolated copy of scripts/ inside a fresh git repo (BATS_TEST_TMPDIR — bats
# creates and removes this per-test on its own), never against this repo's
# real .claude/stack-cache.json or git history.
setup_fake_repo() {
  REPO="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$REPO/scripts"
  # Exclude tests/ itself — otherwise a fake repo's own run-checks.sh would find
  # scripts/tests/ present, see `bats` on PATH, and recurse into this same suite.
  for f in "$BATS_TEST_DIRNAME"/../*; do
    base="$(basename "$f")"
    [[ "$base" == "tests" ]] && continue
    cp -R "$f" "$REPO/scripts/$base"
  done
  cd "$REPO"
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test"
  git checkout -q -b feature/test-branch
}

write_stack_cache() {
  # write_stack_cache <field>=<value> [<field>=<value> ...]
  mkdir -p .claude
  local fmt="" lint="" tc="" test_cmd="" build="" license="" vuln="" stack="fake"
  for kv in "$@"; do
    case "$kv" in
      format=*) fmt="${kv#format=}" ;;
      lint=*) lint="${kv#lint=}" ;;
      typecheck=*) tc="${kv#typecheck=}" ;;
      test=*) test_cmd="${kv#test=}" ;;
      build=*) build="${kv#build=}" ;;
      licenseCheck=*) license="${kv#licenseCheck=}" ;;
      vulnerabilityCheck=*) vuln="${kv#vulnerabilityCheck=}" ;;
      stack=*) stack="${kv#stack=}" ;;
    esac
  done
  cat > .claude/stack-cache.json <<EOF
{
  "stack": "$stack",
  "packageManager": "fake",
  "format": "$fmt",
  "lint": "$lint",
  "typecheck": "$tc",
  "test": "$test_cmd",
  "build": "$build",
  "licenseCheck": "$license",
  "vulnerabilityCheck": "$vuln",
  "generatedAt": "1970-01-01T00:00:00Z"
}
EOF
}
