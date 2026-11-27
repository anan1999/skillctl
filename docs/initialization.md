# Guided setup

Requires Python 3.10+ and Git. Run from your skillctl checkout:

```sh
python -X utf8 skill_manage.py init
```

Setup runs once and exits. It doesn't start a server or call a model.

## What the questions mean

1. **Source:** an existing local folder containing upstream skills. Press Enter
   to use this checkout's examples and manager skill. A source can be a Git
   checkout, a collection of skills, or one skill folder. Setup doesn't fetch it.
2. **Project:** the project whose skills you want to manage. The default is the
   current directory, which may be the skillctl checkout; enter your actual
   project path if different. Only this project and standard user locations are
   inspected, not the whole disk or parent projects.
3. **Target:** choose a numbered directory or enter a custom path. Existing
   directories appear first. Multiple choices use commas, for example `1,3`.
   Empty / not-yet-created directories remain selectable. There is no default
   target: Enter or `q` cancels at this step.
4. **Receipts:** reuse the same state directory if you previously used skillctl.
   It contains management history and rollback backups. Setup suggests the old
   `~/.codex/skill-manager-state` if it exists; otherwise it suggests `state` next
   to the new configuration. You can change the suggestion before saving.
5. **Save:** inspect the exact source, targets and state paths. Only `y`, `yes`
   or `是` saves. Enter, `n`, Ctrl+C or end-of-input cancels.

Setup creates only `~/.config/skillctl/manager.json` and its parent directories.
It does not create target folders or state, install skills, adopt existing copies,
change Gateway settings, collect logs, or mark anything as tested.
Existing configuration is shown and left unchanged, including when `--yes` is
supplied. An invalid existing config is reported, not replaced. Review it manually
before changing it; changing the receipts path disconnects existing history.

The directory list does not prove an agent actually loads that directory.
Codex `.agents/skills` and legacy `.codex/skills` remain separate choices.
Linked directories are not selected silently; choose their actual resolved path.
Sources, targets and management data must not overlap.

## Explicit paths and preview-only setup

For an agent or script, pass explicit source and target choices. Noninteractive
setup never guesses a target. Paths may be absolute or relative to your current
working directory; saved paths are absolute.

```sh
python -X utf8 skill_manage.py --config /private/manager.json init --source /path/to/skills-source --target "Project Pi=/path/to/project/.pi/skills" --state /private/receipts --dry-run
```

Inspect the output, then replace `--dry-run` with `--yes` to save. Repeat `--target`
for multiple named locations. `--config` goes before `init`. `--yes` requires
both `--source` and at least one `--target`; it is not automatic discovery consent.
For interactive setup of a particular project, use `init --project /path/to/project`.

On Windows, short paths such as `C:/work/skillctl` and `C:/skill-state` leave more
room for backup filenames when OS long-path support is disabled. Quoted paths
containing spaces and Chinese target labels are supported.

## What to do after setup

Copy the `locations` and `list --target ...` commands printed by setup. These
show locations and skills without altering them. If you kept the bundled source,
follow the [manager installation steps](../README.md#ask-your-agent-to-manage-skills).
Existing files are still unmanaged until you explicitly
[preview adoption](guide.md#manage-the-skills-you-already-have).

Usage and quality report paths are optional and are not invented by setup.
Without them, results remain unavailable / untested, not zero or passed. To
enable these later, use the [configuration reference](../skills/skillctl-manager/references/setup.md).
