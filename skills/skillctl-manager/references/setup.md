# First-time setup

Requires Python 3.10+, Git and a local skillctl checkout containing
`skill_manage.py`. The skill provides the entrypoint and instructions; core
operations continue to use the checkout's `skillctl.py`.

If configuration exists, preserve its source and receipts. Locate the checkout
using, in order: explicit `--repo`, `SKILLCTL_REPO`, the config's `repo` field,
then the entrypoint's parent directories. If it cannot be located, ask for the
checkout path. Downloading or updating a source requires user authorization.

## Guided setup

Run from the checkout:

```sh
python -X utf8 skill_manage.py init
```

Choose a source and named target from the standard user/project directories or
enter a custom path. Review the source, targets and receipts directory before
saving. Existing configuration stays unchanged. Initialization does not install
or adopt skills.

For noninteractive use, provide explicit `--source`, `--target "NAME=PATH"` and
`--state`. Use `--dry-run` to preview, then `--yes` when authorized to save.
A custom `--config` goes before `init`.

## Configuration reference

The default file is `~/.config/skillctl/manager.json`. Use verified absolute
paths. The values below illustrate the format; replace them with actual locations.

```json
{
  "schema_version": 1,
  "repo": "/absolute/path/to/skillctl",
  "source": "/absolute/path/to/skills-source",
  "state": "/absolute/path/to/skill-manager-state",
  "targets": {
    "Codex": "/absolute/home/.codex/skills",
    "Project Pi": "/absolute/project/.pi/skills"
  },
  "codex_home": "/absolute/home/.codex",
  "usage_report": "/absolute/private-reports/usage.json",
  "quality_report": "/absolute/private-reports/quality.json"
}
```

- `source`: existing upstream root, with a catalog or discoverable skill folders.
- `state`: reuse the original receipts directory. Changing it disconnects
  existing management and rollback history.
- `targets`: named installation locations. Codex's `.agents/skills` and legacy
  `.codex/skills` are separate copies; use the actual intended directory.
- `usage_report` and `quality_report`: optional. Omit them or use null when
  unavailable. If `codex_home` and an output path are configured,
  `usage --refresh` can create a usage snapshot.
- `codex_home`: used only for offline usage collection, not authentication keys.
  Quality queries do not run new model evaluations.

On Windows, use forward slashes or correctly escaped backslashes in JSON.
Keep private configuration paths out of public repositories.

Verify the installed entrypoint:

```text
python -B -X utf8 "<skill-dir>/scripts/manage.py" --repo "/absolute/path/to/skillctl" --config "/absolute/path/to/manager.json" locations
```

## Install the manager skill

If your configured source is the skillctl checkout, preview with:

```sh
python skill_manage.py preview install skillctl-manager --target "NAME"
```

Inspect the target and changes. When authorized, use
`apply PLAN_ID --target "NAME"` with the same configuration.

If your everyday source is another skill collection, install the manager from
this checkout using the existing CLI. Replace `--target` and `--state` with the
actual values shown by `locations`, preserving existing history:

```text
python skillctl.py --source "/absolute/path/to/skillctl" --state "/configured/receipts" install skillctl-manager --target "/configured/skills" --dry-run
python skillctl.py --source "/absolute/path/to/skillctl" --state "/configured/receipts" install skillctl-manager --target "/configured/skills"
```

Each harness controls skill discovery. If an existing session has not discovered
the installation, start a new session or explicitly ask the agent to read the
installed `SKILL.md`. Installation alone does not confirm loading. No running
service is required.
