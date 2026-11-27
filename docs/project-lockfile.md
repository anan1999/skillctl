# Pin skills for a project, then restore them on another machine

A project lockfile preserves the **exact installed skill files**, including your
local instructions. It contains per-file SHA-256 hashes, content snapshots and
the Git revision recorded at installation when available. It does not depend on
the original machine's paths or require a running service.

This is a self-contained snapshot, not a manifest that fetches remote Git URLs.
Copy one `skillctl.lock.json` file to restore offline, even when upstream has moved
on or the original checkout is unavailable. Source revisions are provenance from
receipts; content hashes are the authority for what is restored.

## Try the two-machine demo

With Python 3.10+ and Git, from the checkout:

```sh
python -X utf8 lock_demo.py
```

The demo creates two isolated machine directories and a local Git source. It
adds a local instruction, locks the installed version, advances upstream, makes
the original source path unavailable and restores on the second machine. It also
checks local-edit blocking, keep-local and tampered-content rejection.

All files are temporary. To retain the synthetic fixture and result report,
use `--output /short/new/directory` with a directory that does not yet exist.
No real agent, model, camera or board is used.

## 1. Preview a lockfile

First [configure a source and named project target](initialization.md). The target
must already contain installed or explicitly adopted skills. Existing unmanaged
copies must be [adopted](guide.md#manage-the-skills-you-already-have) first so their
local history is not silently invented.

```sh
python -X utf8 skill_manage.py lock create --target "Project" --file /path/to/project/skillctl.lock.json
```

By default this captures all managed installed skills in that target. To select
only some skills, list their names after `create`:

```sh
python -X utf8 skill_manage.py lock create example-skill skillctl-manager --target "Project" --file /path/to/project/skillctl.lock.json
```

The output shows file hashes, source revisions, available baselines and whether
customizations are included. An existing lockfile also gets a before/after entry
comparison. Installed bytes stay unchanged. Preview stores a private, ten-minute
plan, but does **not** create or replace the project lockfile.

Review the proposal, then save it using the returned plan ID:

```sh
python -X utf8 skill_manage.py lock apply PLAN_ID --target "Project"
```

Changed installed files, receipts, baselines or an intervening lockfile edit
invalidate the preview. Plans are single-use and bound to the configured source,
target and receipts directory. A preview is not an authorization token.
An identical existing lockfile returns `plan_id: null`; nothing needs applying.
Rerun `create` after approved skill updates to preview a revised lockfile.

**The lockfile contains full skill content**, including local edits and scripts.
Review it before committing or sharing it, especially when your skills contain
private project instructions. Configuration, conversation logs and management
history are not added, but paths or secrets already written inside a skill are
preserved as part of its content. No upload or Git commit happens automatically.

## 2. Restore on another machine

Install Python and Git, get a skillctl checkout and copy the lockfile into your
project. Run `python -X utf8 skill_manage.py init` to configure the destination
for this machine. Its source can be the bundled checkout or another existing
local folder: restoring uses the lockfile's content, not that source's version.
Use the correct project target and preserve an existing receipts directory.

You can inspect the lockfile without any configuration:

```sh
python -X utf8 skill_manage.py lock inspect --file /path/to/project/skillctl.lock.json
```

Preview its destination, file differences and conflicts:

```sh
python -X utf8 skill_manage.py lock restore --file /path/to/project/skillctl.lock.json --target "Project"
```

If the proposal is correct and authorized, apply the returned plan:

```sh
python -X utf8 skill_manage.py lock apply PLAN_ID --target "Project"
python -X utf8 skill_manage.py lock check --file /path/to/project/skillctl.lock.json --target "Project"
```

Restoration validates the entire lockfile and all selected skills before writing
any installed files. Changed content, unsafe paths or a blocked skill prevent the
whole default restore. Unlisted skills are untouched. The existing operation lock,
receipt snapshots and rollback mechanism are reused. If a later file operation
fails, already-applied skills are compensated; incomplete recovery is reported as
an error, never success.

## Preserve edits on the destination machine

There are two different cases:

- **Edits captured in the lockfile:** restored with the skill. Their original
  upstream baseline is also restored, so ordinary updates continue to protect
  those customizations. Capturing a skill does not certify its behavior.
- **New edits on the destination:** default restore blocks, including local
  deletions, unmanaged copies and previously preserved customizations. It does
  not force an exact match by discarding them.

To restore the other skills while leaving conflicting copies alone, explicitly
preview with `--keep-local`:

```sh
python -X utf8 skill_manage.py lock restore --file /path/to/project/skillctl.lock.json --target "Project" --keep-local
```

Apply the new plan if one is returned. Skipped skills keep their bytes **and their
existing receipts**. The result lists `kept_local` and reports `matches_lock:
false` whenever the installed bytes differ. If every entry is unchanged or
skipped, `plan_id` is null and there is nothing to apply. `lock check` still exits
with code 1 for differences; keeping local edits does not make them match.

An identical unmanaged copy is not silently adopted. A file-integrity check can
report matching bytes while also reporting `managed: false`.

This first version does not automatically merge conflicting destination edits.
Use the existing [reviewed reconciliation workflow](guide.md#understand-and-resolve-a-blocked-update)
with your intended upstream source, then create a new lockfile of the result.
For a restored skill, the existing `preview rollback NAME` and `apply PLAN_ID`
commands remain available. Further unreviewed edits block rollback as usual.

## Scope and limits

- One lockfile describes one selected skill set. Target paths, labels and harness
  selection belong to each machine's private config; they are never auto-selected
  from an imported lockfile. No extra skills are pruned.
- Restore reads no remote URL and executes no skill scripts. It does not pin
  models, providers, API keys, runtime dependencies, permissions or executable bits.
  Python bytecode and `__pycache__` directories are excluded, following the core
  manager's authored-file hashing rules. Without a verified upstream baseline,
  `customized` is unknown (`null`), not a claim that no local instructions exist.
- JSON is UTF-8. Valid UTF-8 content is stored as text; other bytes use base64.
  SHA-256 hashes detect corruption, not authorship. A lockfile is not a signature
  or evidence that a skill is safe or useful.
- Limits: 128 skills, 2 MiB per file, 16 MiB total expanded installed/baseline
  content, 4,096 expanded file entries, and 32 MiB serialized lockfile data.
  Links, path traversal, nonportable Windows names and case/normalization
  collisions are rejected. Use short paths on Windows for rollback snapshots.
- The file contains baseline snapshots needed to protect customizations.
  Keep it out of the managed skill folders and receipt directories themselves.
- Restore and compare checks cover file integrity only. Confirm harness loading
  separately using an actual successful `SKILL.md` read, and behavior with a real
  task. The current host verification is on Windows; other OS hosts are not
  represented as tested.

See [lockfile verification](lockfile-verification.md) for the exercised scenarios.
