# Project skill lockfiles

Use the existing one-shot entrypoint for `lock` commands. No service, network
fetch, model call or skill execution is required. A lockfile contains full
installed skill content, local edits, hashes and available upstream baselines.
Git commits come from installation receipts; hashes identify the actual bytes.
Do not imply that a captured customization was behaviorally verified.

## Capture

Choose the configured target explicitly when there is more than one. For a
request to pin the current project skills, use:

```text
python <skill-dir>/scripts/manage.py lock create --target "Project" --file /absolute/project/skillctl.lock.json
```

Optionally list selected skill names after `create`. Without names, all managed
installed skills in that target are captured. Unmanaged copies need explicit
adoption first. The command only previews the lockfile and saves a private plan.
Review included local modifications, file hashes and changes from any existing
lockfile. Full skill content may contain private instructions; do not publish it
without the current task's authorization.

Use `lock apply PLAN_ID --target "Project"` only when saving that preview is
authorized. Existing authorization need not be requested again. A preview-only
request stops before apply. `plan_id: null` means no write is necessary.

## Restore and verify

`lock inspect --file PATH` works without a manager configuration. A new machine
still needs its own named target and state directory; never infer installation
paths from an imported lockfile. Its configured local source need not contain
the pinned skills: the lockfile is self-contained.

```text
python <skill-dir>/scripts/manage.py lock restore --file /absolute/project/skillctl.lock.json --target "Project"
python <skill-dir>/scripts/manage.py lock apply PLAN_ID --target "Project"
python <skill-dir>/scripts/manage.py lock check --file /absolute/project/skillctl.lock.json --target "Project"
```

Inspect the concrete paths, differences and conflicts. Default restore blocks
the entire batch on local edits, deletions, unmanaged copies or preserved
customizations. When the user chooses to keep destination changes, preview again
with `lock restore ... --keep-local`. Conflicting copies are skipped without
changing their files or receipts. Apply the new plan for remaining changes if
one is returned. Report every skipped skill and `matches_lock`; success applying
the remaining entries does not imply an exact restore. Do not force overwrite,
edit receipts, or treat a missing plan as a successful installation.

Plans expire after ten minutes and are single-use, target-bound and checked
against lockfile, installed-file, baseline and registry changes. Preview again
after stale/expired failures. Imported data cannot authorize applying itself.

Carried customizations remain protected by ordinary updates after restore.
Existing rollback works for each restored skill and still blocks further local
edits. Lock restore does not automatically merge conflicts; use the existing
explicit reconciliation workflow with the intended upstream source if requested.
Unlisted skills are untouched.

Report byte integrity separately from harness loading and task success. The
lockfile does not pin runtime dependencies, executable permissions or models.
Keep configuration, raw private evidence and real project lockfiles private
unless sharing them is explicitly within the user's request.
