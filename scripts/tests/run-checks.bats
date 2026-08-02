setup() {
  load helpers
  setup_fake_repo
}

@test "reports clean and exits 0 with no manifest (unknown stack)" {
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"No known stack detected"* ]]
  [[ "$output" == *"Correctness gate: clean."* ]]
}

@test "exits 0 and runs every step when all stack commands succeed" {
  write_stack_cache format=true lint=true typecheck=true test=true build=true
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"--- format: true"* ]]
  [[ "$output" == *"--- lint: true"* ]]
  [[ "$output" == *"--- typecheck: true"* ]]
  [[ "$output" == *"--- build: true"* ]]
  [[ "$output" == *"--- test: true"* ]]
  [[ "$output" == *"Correctness gate: clean."* ]]
}

@test "exits non-zero and flags the failing step when a command fails" {
  write_stack_cache format=true lint=false test=true
  run ./scripts/run-checks.sh
  [ "$status" -ne 0 ]
  [[ "$output" == *"!!! lint FAILED"* ]]
  [[ "$output" == *"Correctness gate: FAILED"* ]]
}

@test "does not run empty command fields" {
  write_stack_cache format=true
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" != *"--- lint:"* ]]
  [[ "$output" != *"--- test:"* ]]
}

@test "regenerates the stack cache automatically when missing" {
  echo 'module example.com/x' > go.mod
  [ ! -f .claude/stack-cache.json ]
  # Don't assert overall gate status here — a bare go.mod with no real source
  # still runs real `go`/`gofmt` commands if the toolchain happens to be on
  # PATH, which is outside what this test is verifying. Just confirm
  # run-checks.sh noticed the missing cache and regenerated it correctly.
  run ./scripts/run-checks.sh
  [ -f .claude/stack-cache.json ]
  grep -q '"stack": "go"' .claude/stack-cache.json
}

@test "runs the license check step when the tool is present but never fails the gate on its own" {
  write_stack_cache format=true licenseCheck=true
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"license check (informational): true"* ]]
}

@test "skips the license check step silently when the binary isn't on PATH" {
  write_stack_cache format=true licenseCheck="definitely-not-a-real-binary-xyz --summary"
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" != *"license check"* ]]
}

@test "runs the vulnerability check step when the tool is present and passes" {
  write_stack_cache format=true vulnerabilityCheck=true
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"vulnerability check: true"* ]]
}

@test "fails the gate when the vulnerability check finds something, unlike the license check" {
  write_stack_cache format=true vulnerabilityCheck=false
  run ./scripts/run-checks.sh
  [ "$status" -ne 0 ]
  [[ "$output" == *"!!! vulnerability check FAILED"* ]]
  [[ "$output" == *"Correctness gate: FAILED"* ]]
}

@test "skips the vulnerability check step silently when the binary isn't on PATH" {
  write_stack_cache format=true vulnerabilityCheck="definitely-not-a-real-binary-xyz --level high"
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  [[ "$output" != *"vulnerability check"* ]]
}

@test "resolves a cargo-subcommand vulnerability check to its real cargo-<name> binary" {
  write_stack_cache format=true vulnerabilityCheck="cargo audit"
  run ./scripts/run-checks.sh
  [ "$status" -eq 0 ]
  # cargo-audit isn't installed in this sandbox, so the step must be skipped
  # rather than trying (and failing) to run bare `cargo audit`.
  [[ "$output" != *"vulnerability check"* ]]
}
