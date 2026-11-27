# Onboarding verification — 2026-11-27

## English presentation follow-up

The README, demo, initialization prompts, manager CLI explanatory notes and
first-time setup reference now use English. The public demo screenshot was
regenerated from a successful file-management run. Unicode names and paths are
still supported; existing localized reference documents remain available.

The affected initializer, demo and manager suites ran **22 tests: 21 passed and
1 skipped** because this Windows account cannot create symlinks. The offline
Chrome checks also passed again: all steps, merged content, replay, refresh,
keyboard activation, 390-pixel layout, JavaScript-disabled fallback, and no
external requests or page errors. The English desktop screenshot was visually
checked. This wording update did not rerun the full suite described below.

## Initial onboarding implementation

This change adds guided initialization and replaces the original drift-only demo
with a three-step walkthrough ending in a verified merge. The README now leads
with the problem, runnable demo and setup; advanced commands remain in the
[reference](guide.md). The screenshot is from the actual offline demo report.

## File and CLI checks

`python -B -X utf8 -m unittest discover -s tests -v` completed **130 tests:
128 passed and 2 skipped** on Windows. Both skips require symlink creation, which
this test account cannot perform. Those checks are not counted as passing.

The new initialization tests cover:

- Preview and cancellation leave all files unchanged.
- Saving creates only configuration, not target or receipt directories.
- An existing installation reuses its receipt history without changing bytes.
- Empty locations, Chinese labels, spaces and multiple targets.
- Rerunning setup preserves existing configuration, including invalid files.
- Noninteractive setup requires explicit source and target choices.
- Invalid, duplicate or overlapping paths fail before writing.
- A configuration created during confirmation is not overwritten.
- End-of-input cancels; linked target rejection has a separate skipped test.
- The CLI works from another working directory, then produces a usable install preview.

The demo test exercises real installs, a rejected ordinary update, a read-only
merge preview, an applied merge preserving both changes and an unchanged second
installation. Report content is HTML-escaped. The temporary example's newlines
are normalized **before** its first installation so Git checkout line-ending
settings do not manufacture unrelated edits; actual user files are not normalized.

## Interactive Windows walkthrough

In a real terminal, setup listed available standard directories. An isolated Pi
project was explicitly selected, the proposed configuration was reviewed, and
`y` saved it. Before any install, the target was empty and no state directory
existed. The resulting config then supported:

1. `locations` and `list` (two bundled source skills).
2. `preview install example-skill`, without creating the installed skill.
3. `apply PLAN_ID` into only the isolated target.
4. `doctor example-skill`, reporting `ok: true`, `current` and matching source /
   receipt hashes.
5. A second `init`, leaving the configuration byte-for-byte unchanged.

Source skills also remained unchanged. Private config, receipts and raw results
remain outside Git. This did not install into a real agent's skills directory.

## Offline browser checks

`tests/demo_browser.cjs` used headless Chrome with networking disabled:

- Step buttons show the local instruction, actual patch and merged result.
- Full merged content expands; replay returns to the first step.
- Refresh works offline without a server.
- Keyboard activation works.
- Every step fits a 390-pixel viewport without horizontal overflow.
- All steps remain readable with JavaScript disabled.
- No external HTTP requests or page errors occurred.

Desktop and mobile captures were visually checked. The public image is
[`demo-preview.png`](demo-preview.png); it contains synthetic example content.
To repeat the browser checks after generating the demo:

```sh
python -X utf8 demo.py --output .demo/onboarding.html
node tests/demo_browser.cjs .demo/onboarding.html
```

The optional browser checker requires a local Playwright installation and Chrome.
Set `SKILLCTL_PLAYWRIGHT` to an existing Playwright module path if needed. Running
the demo or initialization itself has no Playwright dependency. Create `.demo`
first if it does not exist, and choose a fresh report filename on a repeat run.

## Limits

This validates local configuration and file operations on Windows, plus browser
behavior. It does not launch Pi, Codex, Claude Code or Cursor, validate their skill
discovery behavior, call an AI Gateway, or operate a device. Mac/Linux interactive
setup has not been exercised on real hosts in this change. The walkthrough's
30 seconds is reading time, not a guaranteed execution duration.
