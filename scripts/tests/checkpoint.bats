setup() {
  load helpers
  setup_fake_repo
  write_stack_cache format=true lint=true typecheck=true test=true build=true
  git add -A
  git commit -q -m "chore: initial"
}

@test "does nothing on main/master/trunk/develop" {
  for b in main master trunk develop; do
    git checkout -q -b "$b" HEAD 2>/dev/null || git checkout -q "$b"
    echo "change" > "file-$b.txt"
    before="$(git rev-parse HEAD)"
    run ./scripts/checkpoint.sh
    [ "$status" -eq 0 ]
    after="$(git rev-parse HEAD)"
    [ "$before" = "$after" ]
    rm -f "file-$b.txt"
  done
}

@test "does nothing when there is nothing to commit" {
  before="$(git rev-parse HEAD)"
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to commit"* ]]
  after="$(git rev-parse HEAD)"
  [ "$before" = "$after" ]
}

@test "commits ordinary changes on a feature branch when the gate is clean" {
  echo "hello" > notes.txt
  before="$(git rev-parse HEAD)"
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"Checkpoint committed"* ]]
  after="$(git rev-parse HEAD)"
  [ "$before" != "$after" ]
  git show --stat HEAD | grep -q notes.txt
}

@test "does not commit when the correctness gate fails" {
  write_stack_cache lint=false
  echo "hello" > notes.txt
  before="$(git rev-parse HEAD)"
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"gate is not clean"* ]]
  after="$(git rev-parse HEAD)"
  [ "$before" = "$after" ]
  # the change is still there, uncommitted — never silently discarded
  [ -f notes.txt ]
}

@test "skips secret-like filenames but still commits ordinary files alongside them" {
  echo "SECRET=shh" > .env
  echo "hello" > notes.txt
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping likely-secret file"* ]]
  [[ "$output" == *".env"* ]]
  git show --stat HEAD | grep -q notes.txt
  ! git show --stat HEAD | grep -q "\.env"
  # .env is left in the working tree, untracked — not discarded, just not committed
  [ -f .env ]
  git status --porcelain .env | grep -q '^??'
}

@test "skips entirely when only secret-like files changed" {
  echo "SECRET=shh" > .env
  before="$(git rev-parse HEAD)"
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"only secret-like files changed"* ]]
  after="$(git rev-parse HEAD)"
  [ "$before" = "$after" ]
}

@test "never runs when not inside a git work tree" {
  cd "$BATS_TEST_TMPDIR"
  mkdir -p notagitrepo
  cp -R "$REPO/scripts" notagitrepo/scripts
  cd notagitrepo
  run ./scripts/checkpoint.sh
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
