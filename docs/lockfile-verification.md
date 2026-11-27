# Project lockfile verification — 2026-11-27

The lockfile implementation uses the existing `skillctl.Manager` to record
installation snapshots, upstream baselines and rollback history. It adds a
portable content container and private preview plans, not another installation
service. Imported snapshots are validated and staged before any installed file
is replaced. All commands and documentation for this feature use English.

## Two-machine CLI acceptance

`python -X utf8 lock_demo.py --output /short/new/directory` completed these
**11 checks** on Windows using synthetic skills and a real temporary Git source:

1. Create preview leaves installed skills and the intended lockfile untouched.
2. The captured version includes the local project instruction.
3. Pins retain the installed Git commit after upstream advances.
4. Restore preview creates no installed skills.
5. A second machine directory restores exactly after the original source path
   is made unavailable.
6. `lock check` confirms both skills' bytes.
7. Ordinary updates still block overwriting the carried customization.
8. New local changes block the restore without partially writing another skill.
9. `--keep-local` preserves bytes and reports a nonmatching result.
10. `lock check` reports divergence instead of success.
11. An object with altered content and an unchanged hash is rejected before writes.

The demo uses separate Pi-style and Codex-style project paths. Those directory
names are fixtures: no harness application is launched. It never reads real
conversation logs, contacts a model provider or operates a device.

## Additional regression scenarios

The full suite completed **147 tests: 145 passed and 2 skipped** on Windows
(`python -B -X utf8 -m unittest discover -s tests -v`, approximately 447 seconds).
Both skips require symlink creation, which this account cannot perform. The 17
lockfile tests all passed. After the final review adjustments, the affected
local-only reporting, portable-path/size validation, registry-staleness and
offline restore/rollback cases were rerun and passed.

`tests/test_lock.py` covers exact binary and text bytes, deterministic recapture
on the destination, source revision provenance, clean managed updates, preserving
unlisted skills, ordinary rollback, and keeping customizations protected after
restore. It also verifies:

- An unmanaged identical copy is not silently adopted.
- Local deletion stays deleted under `--keep-local` and is reported missing.
- Local-only adopted skills can be carried without claiming a known upstream
  baseline or falsely reporting that customization is absent.
- Altered baseline snapshots stop both capture and restore.
- Arbitrary existing output files are never replaced by a lockfile.
- Changed lockfiles, installed files and receipts invalidate previews.
- Expired, reused and wrong-target plans are rejected; operation locks are honored.
- A failure on the second skill compensates the first; existing files and exact
  registry bytes are restored. A fresh target is left without installed skills.
- Invalid schemas, hashes, traversal paths, Windows reserved names, case and
  directory collisions, and content expansion beyond configured limits are rejected.
- `lock inspect` runs without a configuration, and the CLI can preview, apply
  and check restoration through a configured target.
- One offline test forbids both socket creation and subprocess execution during
  restore, confirming that the lock content is sufficient without Git/network calls.

## Limits of the evidence

This validates skill file management on the current Windows host. Two directories
simulate separate machines; a second physical computer, Linux and macOS have not
been exercised for this feature. Existing symlink-dependent tests remain subject
to this host's Windows permissions. Integrity checks do not establish authorship,
agent loading, runtime dependencies, executable permissions or successful AI tasks.

Raw configurations, capture/restore plans, snapshots and demo results stay local.
The repository contains the reproducible demo and tests, not a lockfile of real
personal skills. See the [usage guide](project-lockfile.md) for limits and commands.
