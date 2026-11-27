# On-demand workflow trial — 2026-11-27

Implemented learning receipts (`learn record/list/show`), factual update impact
summaries (`impact`), and bounded paired SKILL.md text comparisons
(`compare prepare/run`) through the existing manager entrypoint.

## Validation

- Regression run: 112 tests, successful with one skip. After the final request
  binding and input-size changes, all 12 focused workflow tests passed.
- Skill frontmatter validation and the skillctl reference/structure check passed.
- The isolated `workflow_demo.py` walkthrough ran successfully with its explicitly
  labeled fixture adapter; it makes no model requests.
- A separate real model trial used the existing host Gateway through the installed
  gateway-delegate helper. Model: `gemini-3.8-flash`; four requests, one per
  version/case; no retries. Each request allowed at most 1,024 output tokens and
  60 seconds, with a 300-second overall local deadline. Only public example
  instructions and synthetic timestamp inputs were sent.

| Case | Old | New | Comparison |
| --- | --- | --- | --- |
| Both timestamps available; compute 5 seconds | Passed | Passed | Unchanged |
| Camera timestamp absent; report unknown | Failed (zero) | Passed (null) | Improved |

The adapter reported 2,062 total tokens. Monetary cost is unknown. These two
cases demonstrate the comparison mechanism and the specific wording correction,
not general quality, real harness loading, or device behavior. Expected answers
were retained in the local grader and not sent in model jobs.

Focused tests covered missing/changed evidence, explicit project scope, duplicate
creation rejection, receipt tampering, read-only impact and drift explanation,
preflight call limits, changed skills, request/context binding, input-size limits,
regression visibility, malformed responses, truncation, timeout, no automatic
retry, unknown usage, and rejection of a reused run directory.

Initial sandbox runs encountered Windows temporary-file rename and loopback
socket restrictions. The successful regression run used normal local permissions
and isolated test data. No persistent service was left running.

Real installed skills and prior integration drafts were not changed. Private
receipts, Gateway adapter paths, model responses and local configuration remain
outside tracked release artifacts. This trial did not commit, push or sync skills.

## Complex acceptance and publication candidate

Final full regression: 116 tests, successful with one Windows symlink capability
skip (115 passed). Skill frontmatter and reference validation also passed.

The follow-up acceptance ran actual isolated file operations across two simulated
agent locations. Eight checks passed: ordinary update blocking; stale-source
preview rejection without partial writes; non-overlapping merge; overlapping
conflict rejection; keep-local preservation; content/baseline rollback; rejection
of a plan for another target; and detection of changed learning evidence.
The script is `workflow_acceptance.py`, also exercised by the regression suite.
An initial run under the deeply nested checkout exceeded the existing Windows
snapshot path-length limit; the successful run used a shorter workspace path.

The overall comparison now reports `candidate_assessment` independently of run
completion. A deterministic mixed-result test verifies that two improved cases
cannot conceal one regression; a candidate failing checks on both versions is
reported as failed_checks, not passed merely because results were unchanged.

A separate six-case Gateway comparison used `gemini-3.8-flash`, at most 12 calls,
1,024 output tokens per call, 60 seconds per call and a 600-second local overall
deadline. Both the installed old entrypoint and candidate new entrypoint passed
all six cases, with no regression observed. The cases covered injected text in
preview evidence, higher aggregate scores concealing a regression, incomplete
evaluation, learning receipts versus independent verification, project scope,
and read counts versus successful task counts. The adapter reported 22,248 tokens;
cost is unknown. This establishes parity on these cases, not an improvement or
general proof of safety. Only SKILL.md text was supplied, not references or tools.
The [synthetic suite](../quality/manager-workflow.json) and
[sanitized comparison result](../quality/manager-workflow-result.json) preserve
case-level checks, version hashes and reported usage for review. Raw plans,
provider receipts and local paths remain private.

The checked-in AGENTS.md makes the coordination/learning workflow reproducible
without a server, a provider-specific dependency, or machine-specific settings.
Host adoption is a separate managed installation, not implied by this document.
