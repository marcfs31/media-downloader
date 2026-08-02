# ast-grep structural lint

`rules/` is scanned by `scripts/run-checks.sh` (only when `ast-grep` is installed
and at least one rule file exists there — it's empty by default, so a fresh
checkout is never surprised by rules nobody activated).

`rules-examples/` holds copy-paste starting points, one per demonstrated pattern.
To activate one: `cp rules-examples/no-console-log.yml rules/`.

Write your own with `ast-grep new rule` or by hand — a rule is a YAML pattern
matched against the real parse tree (via tree-sitter), not a regex, so it won't
false-positive on a string that happens to contain `console.log(`.

See https://ast-grep.github.io/ for the full rule syntax and supported languages.
