// Enforces the Conventional Commits type prefix used by this template's branch
// naming (see CLAUDE.md > GitHub conventions): feat/fix/chore/refactor plus the
// handful of standard extras (docs/test/ci/build/perf/style/revert) that don't
// warrant their own branch prefix but are still useful commit-type signal.
//
// Inert until the consuming project installs the packages and runs
// `lefthook install` — see lefthook.yml's commit-msg hook, which skips silently
// if commitlint isn't installed:
//   pnpm add -D @commitlint/cli @commitlint/config-conventional
export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // This repo's existing commit history capitalizes the subject's first word
    // ("Add PWA installability", not "add PWA installability") — only forbid
    // full upper-case (SCREAMING SUBJECT), don't fight the established style.
    "subject-case": [2, "never", ["upper-case"]],
  },
};
