# Try skills on a task before adopting them

The experimental `task` commands connect three on-demand operations:

1. Find candidates in your configured local source and compare them with a
   no-skill baseline on copies of the same Python task.
2. Remember a failed check so future plans for that task cannot silently drop it.
3. Save a passing candidate as a version-pinned recipe and prepare a replay on
   another project with different inputs.

No daemon, automatic installation, background observation, or global transcript
scan is involved. This first version supports **text proposals for Python file
edits**, followed by actual local verifier processes. It is not an autonomous
Codex/Pi/OpenHands session or proof that any harness natively loaded a skill.
Only `SKILL.md` text is supplied; referenced files and skill tools are not loaded.

## Start with the offline example

```sh
python -B -X utf8 task_demo.py
```

The synthetic example compares a correct telemetry contract with an obsolete
contract. Both can convert ordinary readings; only one treats the device's
`-9999` sentinel as unavailable. The demo runs four checks per variant, saves the
failed sentinel check, blocks a plan that removes it, then replays the good
recipe against another input set. It leaves the original project unchanged.

The adapter is deterministic and explicitly labeled **fixture**. This validates
the machinery, not AI quality. Fixture results cannot authorize an adoption
preview. The output directory contains the spec, source skills, configuration,
plans, per-file `changes.diff`, reports, recipe, and result workspaces.

Use `--output /short/new/path` to choose a new output directory. To create just
the synthetic inputs for your own trusted model adapter:

```sh
python -B -X utf8 task_demo.py --setup-only --output /short/new/path
```

## Describe a bounded task

Configure a local source and named target using [guided initialization](guide.md).
Create a JSON task spec:

```json
{
  "schema_version": 1,
  "id": "telemetry-parser",
  "task": "Repair the telemetry parser according to its contract.",
  "files": ["parser.py", "samples.json", "verify.py"],
  "context_files": ["parser.py", "samples.json"],
  "write_files": ["parser.py"],
  "checks": [
    {"id": "sentinel", "argv": ["{python}", "verify.py", "sentinel"], "timeout": 5}
  ]
}
```

Only the explicitly listed UTF-8 files are copied. Context files are sent to the
model. The model can propose complete replacements only for `write_files`.
Verifier scripts must be listed, protected, and absent from model context.
Each verifier must emit JSON such as `{"passed": true, "detail": "sentinel passed"}`
and exit 0 for pass or 1 for fail. Exceptions, malformed output, inconsistent
exit status, output limits, and timeouts are **inconclusive**, never zero or pass.
Verifier/program changes to workspace files invalidate the result.

The v1 limit is 32 files, 128 KiB per file, 512 KiB selected content, 12 checks,
and 64 KiB per `SKILL.md`. Paths must be portable and unlinked. Checks use the
current Python interpreter with no extra package installation.

## Find, preview, run

All examples below use the default manager configuration. Add
`--config /path/to/manager.json` **before** `task` for another configuration.

```sh
python skill_manage.py task find --spec task.json --project /path/to/project --target Project
python skill_manage.py task prepare --spec task.json --project /path/to/project --skills telemetry-contract telemetry-legacy --model YOUR_EXISTING_MODEL --max-calls 3 --max-tokens 2048 --timeout 60 --total-timeout 300 --output /path/to/new-preview --target Project
python skill_manage.py task run --plan /path/to/new-preview/plan.json --adapter /private/adapter.json --output /path/to/new-run --target Project
```

Without `--skills`, preparation picks the top two local lexical matches. Results
show matched terms; this is not semantic routing or a quality ranking. A few
Chinese symptom synonyms are supported. No matches require an explicit choice.

Preview binds project bytes, all selected skill file hashes, configuration,
remembered checks, model, and budgets. Changes require a new preview. Baseline
and candidates start from identical selected files, use the same task/model,
and run sequentially. There are 2–4 calls total (baseline plus 1–3 candidates),
no retries, and no provider/model fallback. An execution error halts later calls.

Report `ok` means the evaluation completed with current evidence, not that every
candidate passed. Read each variant's status and checks. `original_unchanged`
covers the explicitly selected original files; it is not a whole-host audit.

The local adapter file has `{"kind":"model", "argv":["/absolute/python", "/private/adapter.py"]}`.
It reads one JSON request from stdin and emits one JSON response on stdout. The
request contains `id`, `model`, `task`, `context`, `max_tokens`, `timeout`, and
`request_sha256`. The response must echo `id`, `model`, `request_sha256`, set
`status` to `completed`, and return `answer` as:

```json
{"files": {"parser.py": "complete replacement text"}, "summary": "What changed"}
```

Optional `usage.total_tokens` is recorded. Missing usage and cost remain unknown.
Per-variant seconds include generation and verification; they are not pure model
latency. A single trial is not a statistical benchmark. Every per-check regression
against baseline remains visible even if another check improves.

## Remember a failure and replay success

```sh
python skill_manage.py task remember --report /path/to/new-run/report.json --candidate telemetry-legacy --check sentinel --target Project
python skill_manage.py task recipe --report /path/to/new-run/report.json --candidate telemetry-contract --output /private/recipe.json --target Project
python skill_manage.py task replay --recipe /private/recipe.json --project /path/to/another-project --output /path/to/replay-preview --target Project
python skill_manage.py task run --plan /path/to/replay-preview/plan.json --adapter /private/adapter.json --output /path/to/replay-run --target Project
```

A remembered guard comes from an actually failed, completed check. Its exact
definition and protected verifier hashes are required in future plans for the
same task ID and configured target. It does not silently alter task specs, add
tests to unrelated projects, or intercept ordinary installs/updates. Changing a
guard requires explicit review; there is no automatic reset or weakening.

A recipe binds the skill's file hashes, task spec, model, limits, verifier hashes,
and source report digest. It saves the procedure, not a claim that all future
inputs will pass. Replay permits different context/write inputs, preserves the
verifiers, and creates a fresh baseline-plus-candidate preview. Skill/verifier
changes block replay. Saving a recipe does not call a model or install anything.

## Adopt only after review

```sh
python skill_manage.py task adopt --report /path/to/replay-run/report.json --candidate telemetry-contract --target Project
```

This creates a normal installation **preview**, only for a complete passing
model-backed candidate with no regressions and current evidence. Changed
workspaces, verifier output, source versions, configuration, or guards block it.
After inspecting the resulting diff, an authorized `apply PLAN_ID` uses the
existing local-edit protections. Trial results do not grant installation
permission and do not automatically modify or lock the project.

## Trust and evidence limits

These are separate working copies, **not an OS sandbox**. Generated Python and
verifiers run as the current user and can access the host. Use trusted synthetic
tasks or an externally isolated environment; the runner cannot safely execute
arbitrary marketplace code. A passing verifier is observed test evidence, not a
security certificate or proof against a malicious program that spoofs output.

The model adapter is trusted executable code. `kind=model` is a provenance label,
not cryptographic remote attestation. SHA-256 detects accidental evidence drift;
someone who can rewrite the artifacts and their hashes can forge them. Keep
adapters, plans, copied code, prompts, responses, guards and recipes private.
Review their contents before sharing. Credentials and adapter paths are not
embedded in portable recipes.

Distinguish these outcomes when reporting: file installed; SKILL text supplied
to an adapter; native harness read observed; text checks passed; program checks
passed. This workflow directly observes only the supplied text and local checks.

See [Windows verification results](task-trials-verification.md) for the offline
walkthrough, live model trial and their limits.
