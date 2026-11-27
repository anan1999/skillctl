# On-demand task trials

Use `skill_manage.py task` through the existing manager wrapper for requests such
as finding skills for a task, remembering a failed check, or replaying a successful
procedure. Read `<repo>/docs/task-trials.md` for the specification and adapter
protocol. `python <repo>/task_demo.py` is a deterministic offline walkthrough.

- `task find`: search the configured local source, showing lexical match reasons.
- `task prepare`: preview baseline and selected candidates with a fixed task,
  model, budgets, project hashes and skill versions.
- `task run`: call the explicitly authorized adapter and execute trusted Python
  verifier scripts on separate copies. No retry or model substitution.
- `task remember`: save an observed failed check for this task ID and target.
- `task recipe`: save a passing, nonregressing procedure with pinned skill and
  verifier hashes. This does not execute it.
- `task replay`: prepare a fresh baseline/candidate plan for another project.
- `task adopt`: create an installation preview from complete passing model
  evidence. Apply only within the user's current authorization.

Use explicit named targets. The prototype sends only selected context files and
SKILL.md text; it does not invoke a native harness or read skill reference files.
Preserve the distinction between fixture evidence, model proposals, local test
results and native harness reads. Report incomplete evaluations as inconclusive.
Separate copies are not an OS sandbox: generated code and checks run as the
current user. Use trusted tasks or external isolation. Do not try this on a board
or turn a skill management request into device operations.

Remembered checks apply to this workflow, not all normal installs or updates.
Keep raw plans, prompts, responses and local evidence private. A single passing
trial does not prove general improvement; report individual regressions and
unknown usage/cost without smoothing them into an aggregate success claim.
