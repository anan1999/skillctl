# On-demand manager verification — 2026-11-26

The new skill uses the existing `skillctl.Manager` operations through a one-shot
CLI. No HTTP listener, model provider or background worker is required.

## Automated checks

The full suite exercised 102 tests. Adding a second public catalog entry exposed
the existing harness fixture's assumption that its source contained only the
example skill. The fixture now explicitly contains that one example, preserving
its original install, doctor and rollback assertions. All 8 harness tests were
rerun after this correction, including all 8 harness/scope combinations.

All 8 new manager tests were also rerun against the final implementation:

- Read-only Chinese search, quality hash staleness and unsupported usage scope.
- Usage refresh and 7/30-day queries with synthetic logs and no network.
- Preview/install/update/rollback with source, target and receipt hash checks.
- Expired, reused, stale and wrong-target previews rejected.
- Local edits block update; keep preserves bytes; merge preserves independent edits.
- Changed source configuration and traversal rejected.
- The published manager package installs via skillctl and runs from an unrelated
  working directory using its explicit configuration.

Combined full-suite and affected-suite reruns cover **101 passing tests and one
existing Windows symlink-privilege skip**. Skill creator validation also passed.

## Independent Chinese workflow

An independent agent received only the skill, a synthetic configuration and a
Chinese request to query usage/quality, install one skill into Pi, and preview
another update without changing it. It read SKILL.md and the command reference,
reported the 7-day 3-read/2-conversation and 30-day 8-read/4-conversation fixtures,
identified a stale quality report, previewed and applied the authorized install,
then stopped at the existing local modification conflict.

The coordinator independently verified that source files and the existing Codex
copy were unchanged; the new Pi copy matched its source and installation receipt.
This tests instructions and CLI behavior, not Pi agent runtime loading.

## Real local acceptance

The skill was installed in the local Codex skills directory using preview/apply.
All installed files matched the package and receipt. The installed SKILL.md was
read through an agent tool.

The previously running management-page server was stopped and its port confirmed
closed. From an unrelated working directory, the installed entrypoint successfully
listed configured locations, searched Chinese symptoms, refreshed local usage,
queried both periods, read an existing quality report and diagnosed its own
installation. The newly created skill appeared in confirmed read evidence.

No device was accessed. Private configuration, logs, snapshots, receipts and real
usage reports stay local and are not published. Confirmed reads remain a lower
bound; quality reports remain text simulations tied to file hashes.
