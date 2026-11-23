# Windows verification

Date: 2026-11-23. Native Windows, Python 3.11 and Git. These are bounded local
tests, not a claim that every version or configuration of each harness works.

## Filesystem and CLI tests

Run `python -m unittest discover -s tests -v`.

Result: **35 tests passed**. The final scan scope-filter adjustment was also
rerun separately and passed. Both demonstrations below passed end to end.

The suite covers Pi, Codex, Claude Code and Cursor in both project and isolated
user scopes: actual CLI install, hash verification, doctor, drift refusal and
rollback. The adoption/reconciliation CLI flow runs against all four project
targets. Additional cases cover:

- Catalog-free discovery and in-place adoption without rewriting installed bytes.
- Locally adopted skills without an upstream, and explicit later source binding.
- Non-overlapping text edits, overlapping conflicts, binary conflicts, additions,
  delete/modify conflicts and file/directory collisions.
- Keep-local followed by a future merge, with ordinary install still protecting edits.
- Verified baseline snapshots, legacy receipts, tampered baseline refusal,
  manual resolution and unresolved-marker refusal.
- Failed receipt persistence compensation, rollback, read-only previews and scans.
- BOM/line-ending/empty-file differences and invalid merged local references.

Both `python reconcile_demo.py` and `python demo.py --output report.html` were
executed. The first verifies adoption through merge, conflict, keep and rollback;
the second verifies the original two-machine drift demonstration.

## Live harness tests

The probe skill was installed by skillctl into a dedicated project target. Its
body instructs the agent to read `references/proof.txt`, which contains a fresh
random marker. The prompt supplies the skill name but neither its contents nor
the marker. Success requires actual tool-read evidence and the marker in the
response, not just an assistant claiming to have used a skill.

| Harness | Version | Actual result |
| --- | --- | --- |
| Codex CLI | `0.167.0-alpha.4.1` | Passed. Read-only session: successful `Get-Content` tool executions for `.agents/skills/skillctl-probe/SKILL.md` and its proof file, both exit 0; final response contained the marker. |
| Pi CLI | `1.5.0` | Passed. Existing custom gateway configuration: successful `read` tool results for `.pi/skills/skillctl-probe/SKILL.md` and its proof file; `isError: false`; marker returned. |
| Orca Pi Chat | Orca `1.4.262.0`, Pi `1.5.0` | Passed through the native chat UI in an existing test workspace. Session log recorded successful reads of the same two project skill files and the marker in the response. Existing gateway/model selection was retained. |
| Claude Code | Installed locally | Directory management passed; model execution stopped with `Credit balance is too low`. Runtime skill loading is **not verified**. |
| Cursor Agent | Installed locally | Directory management passed; model execution stopped with `Authentication required`. Runtime skill loading is **not verified**. |

After the two account-related failures, the requested live acceptance scope was
narrowed to Codex and Orca/Pi. No login, billing, credentials or model-provider
configuration was changed to work around those failures. No boards or cameras
were contacted. Raw local session logs, private skills and gateway configuration
are excluded from the public repository.

Windows sandbox restrictions initially prevented atomic renames in test temp
directories; the test suite was rerun outside that filesystem sandbox. A deeply
nested state path also hit a Windows path-length limit. Using a shorter state
path resolved it; long-path support is not enabled by this tool.

## Reproduce a live probe

1. Create a fresh local project and a skill named `skillctl-probe` with valid
   `name`/`description` frontmatter.
2. Put a random marker only in its `references/proof.txt`; have `SKILL.md` ask the
   agent to read that file and report the marker without modifying anything.
3. Install with `python skillctl.py --source /path/to/probe-source install
   skillctl-probe --agent pi --project /path/to/project` (or `--agent codex`).
4. Open a fresh native session in that project and request the named probe.
5. Inspect tool events for the exact skill path and successful read result; match
   the returned marker against the local file. Do not count install/doctor alone
   as runtime evidence.
