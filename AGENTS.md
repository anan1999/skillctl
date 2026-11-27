# Working on skillctl

Keep this workflow usable from a one-shot CLI and ordinary skill files. A running
web service is optional. Follow the user's current scope and preserve unrelated edits.

## Coordinator and evidence

- The active coding agent (Codex in this workflow) coordinates the task, checks actual files/results, and reports in the
  original conversation. Delegated answers are proposals until verified.
- When delegation is appropriate and authorized, use an available text delegate
  for bounded analysis; use a file-capable coding workflow for implementation.
  Reuse the user's configured provider. Do not silently retry or switch models.
- Set meaningful acceptance criteria before changing behavior. Scale validation
  to the change: structure/link checks for reference corrections, targeted tests
  for behavior changes, and paired cases when comparing skill versions.
- Separate installation integrity, observed SKILL.md loading, text-simulation
  quality, and real task success. Never turn missing evidence into zero or PASS.

## Learning and updates

When the current task yields a verified, reusable discovery or correction,
proactively propose or record a scoped learning candidate using skillctl-manager
when available. Prefer updating an existing skill over creating a duplicate.
Record evidence locators/hashes and limitations; a learning receipt is not a skill
installation or proof of behavior. Skip routine/no-new-knowledge tasks without
creating busywork. Do not scan all conversations or start a background observer.

Preview the affected target and content before applying updates. Preserve local
guidance; use explicit keep/merge choices, not receipt edits or forced overwrites.
Respect preview-only requests. If current authorization already covers a concrete
conflict-free action, proceed without asking for the same permission again.

For paired comparisons, use bounded calls/output/time, matched cases/model, and
version-bound receipts. Treat errors and missing responses as inconclusive.
Any per-case regression remains visible even if aggregate scores improve.
Passing text checks does not grant install/publish permission.

## Repository checks and publication

- Run relevant `python -B -m unittest discover -s tests -p ...` tests; run the full
  suite for broad changes. Use a short temporary path on Windows for snapshots.
- `python workflow_acceptance.py --output /short/new/path` runs multi-target merge,
  conflict, keep, rollback and evidence-drift checks with isolated files.
- Use `git diff --check` and inspect intended files before committing.
- Keep host configuration, credentials, private prompts/logs, learning receipts,
  real local paths and raw model receipts outside published Git content.
- Publish only when requested, using a normal non-force push. Verify the remote
  commit after pushing. No board, camera, harness installation or Ping sync is
  implied by publishing this repository.
