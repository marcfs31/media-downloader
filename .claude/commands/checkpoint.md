---
description: Manually run the correctness gate and, if clean, commit a local WIP checkpoint (same logic the Stop hook runs automatically)
---

Run `./scripts/checkpoint.sh` and report its output verbatim. This is the same script
the `Stop` hook runs automatically — use this command when you want to force a
checkpoint mid-session (e.g. right before trying something risky) rather than waiting
for the turn to end. It will not commit on `main`/`master`/`trunk`/`develop`, will not
stage anything that looks like a secret, and never pushes.
