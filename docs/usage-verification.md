# Usage dashboard verification — 2026-11-26

The usage collector and dashboard were tested on Windows with local Codex logs.
Real logs, aggregate personal statistics, local configuration, and screenshots
containing personal usage remain outside Git.

- `python -m unittest discover -s tests -v`: **94 tests, 93 passed, 1 skipped**.
  The skip is the existing quality symlink test because the host lacks symlink
  creation privileges. New usage tests cover evidence pairing, 7/30-day boundaries,
  failures, duplicate and inherited events, privacy, malformed/oversized input,
  stale snapshots, atomic refresh, and authenticated read-only HTTP requests.
- `tests/dashboard_usage_browser.cjs`: Chrome passed synthetic 7/30 counts,
  conversation counts, quality coexistence, zero versus unavailable data,
  Pi unsupported status, corrupt-report recovery, reload, and mobile layout.
- Existing quality, guided-task, library and management browser suites passed.
  Install/adopt/merge/keep/rollback operations ran only against disposable fixtures.
- A real local snapshot was collected and configured on the existing dashboard.
  Three installed skills' 7/30-day counts matched the snapshot in Chrome;
  quality results remained visible. Pi status, reload, and mobile layout passed.
  This live test issued GET requests only and did not operate devices.

The collector prototype was produced using OpenHands. Its initial reasoning
attempt and later repeated test-file write failures were retained in the local
delegation record. The coordinator completed the fixes, tests and documentation;
the corrected parser also passed a separate immutable acceptance script in an
isolated candidate copy. No failed worker attempt is represented as a successful
autonomous implementation.

These checks establish the tested formats and UI behavior, not exhaustive parsing
of every Codex version or a count of completed tasks. See
[使用統計操作與限制](usage.zh-TW.md) for coverage and interpretation.
