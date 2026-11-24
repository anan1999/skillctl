# Dashboard verification

Date: 2026-11-24. Native Windows, Python 3.11.2 and local Google Chrome.

Result: **53 Python tests passed**, including eighteen dashboard HTTP tests. Both
Chrome browser workflows passed against isolated demo source/target/state directories.
Desktop and mobile screenshots were visually inspected.

The UI checks cover status filters,
single-line long descriptions, Ctrl+K search, file selection, unified/split diffs,
expanded inspection, Escape/focus restoration and navigation at 390px.

The management workflow additionally verifies cross-target installation into Pi
and Codex directories, cancel and destination-change invalidation, clipboard text,
refresh after installation, update and rollback of previous contents, explicit
first-install removal, invalid SKILL.md diagnostics and matching/different copies.
HTTP tests verify read-only install/rollback previews, stale source/local/receipt
protection, operation locks, modified backup refusal, and rollback with broken upstream.
Invocation prompts and static file checks do not establish that an agent loaded a skill.

The HTTP integration tests use real temporary source/target/state directories,
not mocked Manager responses. They verify read-only scan/detail/preview, adoption,
non-overlapping merge, overlapping conflict refusal, keep-local, rollback of a
merged result, expired and single-use preview IDs, operation-lock refusal, and
stale local/upstream/receipt detection. Foreign origins, missing session tokens,
unconfigured targets, invalid skill names and altered apply parameters are rejected.

The actual browser test clicks the rendered controls and checks resulting files:

- Scan, Chinese description search, empty search and target switching.
- Refresh restores the selected target and rescans only that directory.
- Configuration and loading feedback appear while a scan is pending; unloaded is not shown as zero.
- Failed scans preserve clearly labelled old results, disable their actions and allow retry.
- A 30-second scan timeout releases the controls and allows retry.
- Counts distinguish physical installations, managed receipts and missing upstream skills;
  adoption updates managed counts without changing the installed count.
- Adoption preview leaves files untouched; apply retains existing contents.
- Merge preview leaves files untouched; apply contains both LOCAL and UPSTREAM.
- Overlapping edits render conflict text and disable Apply; keep retains local bytes.
- Editing a file after preview causes Apply to fail without losing that edit.
- Cancel clears the preview; a fresh preview can subsequently apply.
- Skill text containing HTML is displayed as text, not inserted as elements.
- Desktop and 390-pixel mobile layouts; no page errors or dialogs.

## Run the Python checks

```sh
python -m unittest discover -s tests -v
```

## Repeat the browser test (optional developer dependency)

The application itself needs no Node packages. For browser testing, install
Playwright separately and use an already-installed Google Chrome. Start a fresh
demo for each run; the test intentionally applies changes to its disposable skills.

```powershell
New-Item -ItemType Directory -Force .demo | Out-Null
python -X utf8 dashboard_demo.py --info .demo/browser-fixture.json
```

In another terminal in the checkout:

```powershell
npm install --prefix .demo/browser-tools playwright
$env:SKILLCTL_PLAYWRIGHT = (Resolve-Path .demo/browser-tools/node_modules/playwright).Path
node tests/dashboard_browser.cjs .demo/browser-fixture.json
```

Start a separate fresh demo with a new info filename for the management workflow:

```powershell
python -X utf8 dashboard_demo.py --info .demo/management-fixture.json
# In another terminal with the same SKILLCTL_PLAYWRIGHT setting:
node tests/dashboard_management_browser.cjs .demo/management-fixture.json
```

The script records screenshots under ignored `.demo/dashboard-ui/`. Stop the demo
with Ctrl+C afterward. Remove the old info file or choose a new filename before
starting another demo. Do not point this test at real skills or a production page.
No private skills, gateway settings, local screenshots or raw session logs are
included in the public repository.
