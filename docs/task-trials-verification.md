# Task trials verification — 2026-11-27

Candidate selection, remembered regression checks, and reusable recipes were
exercised on Windows with copied synthetic Python projects. No real skills were
installed, and no board, camera, remote host or native harness was operated.

## Offline workflow

`python -B -X utf8 task_demo.py --output /short/new/path` completed local candidate
discovery, four checks per variant, file diffs, saving the failed sentinel check,
blocking its removal, exporting the passing recipe, and replaying it against a
second input set. Original files stayed unchanged. Fixture results remained
ineligible for model-backed adoption previews. This deterministic adapter tests
the workflow machinery, not AI quality.

## Live model trial

The existing private Gateway adapter reused the unchanged `gemini-3.8-flash`
configuration. There were **five calls**, no retries and no model fallback. Each
call had a 2,048 output-token allowance and 90-second timeout; each run had a
360-second total deadline. Only selected synthetic context and SKILL.md text
were supplied; verifiers were withheld. The local runner applied proposed file
replacements and executed the checks in copies. Generated code was inspected.

| Run | Variant | Passed checks | Observed failure |
|---|---|---:|---|
| Initial input | No-skill baseline | 1/4 | Returned a row object instead of the required scalar result |
| Initial input | Current contract | 4/4 | None in these checks |
| Initial input | Legacy contract | 3/4 | Accepted the reserved `-9999` sentinel as valid |
| Different input | No-skill baseline | 1/4 | Returned a row object; remembered sentinel check also failed |
| Different input | Replayed current contract | 4/4 | None in these checks |

The legacy failure was saved from the live report. Removing that check was
blocked. Replay included the guard, and the passing candidate produced an
installation preview. The target remained absent; no apply ran. Both original
project copies remained unchanged.

Reported usage was **5,441 + 3,075 = 8,516 tokens**. Billing cost was not provided
and remains unknown. Candidate regressions against baseline were empty in these
runs; a separate fixture verifies that a baseline pass followed by a candidate
failure remains visible as a regression.

These two small synthetic trials are not a benchmark or a general improvement
claim. The current skill supplies domain knowledge missing from the baseline;
the obsolete skill deliberately contains a wrong domain rule. The checks show
their different effects on this particular task.

## Regression coverage

The full repository suite completed **160 tests: 158 passed, 2 skipped** in
approximately 535 seconds on Windows. Both skips require unavailable symlink
creation privileges. After the final bounded-manifest changes and two added
cases, all **15 focused task tests** passed (approximately 124 seconds). The
manager skill package's reference/file validation also passed.

`tests/test_task.py` covers the complete CLI flow with real verifier subprocesses,
stale project/skill/configuration previews, guarded replay, version and evidence
drift, forbidden edits, invalid/truncated answers, no retries, verifier errors
and timeouts, workspace mutation including bytecode, bounded evidence size,
individual regressions, fixture adoption rejection and preview-only installs.
Model-labeled responses in unit tests are mocks, separate from the live runs.

## Evidence limits

Native Codex/Pi/Claude loading was **not tested**. Supplying skill text is not an
observed harness file read. Passing checks are local program-test evidence, not
proof of instruction compliance on all tasks.

Copies are not an OS sandbox; programs/checks run as the current user. Hashes
detect accidental drift, not forged receipts or malicious programs spoofing
output. The public repository contains synthetic fixtures, implementation,
tests and this aggregate account. Raw receipts, paths, configuration, guards
and learned evidence stay private.

See the [task trial guide](task-trials.md) for commands and limits.
