"""Guided, local-only configuration. Does not install or adopt any skills."""
import json
import os
from pathlib import Path
import shlex
import sys

from skillctl import AGENTS, Manager
from skillctl_content import is_link


REPO = Path(__file__).resolve().parent


def add_parser(sub):
    parser = sub.add_parser('init', help='Choose skill locations and preview a new configuration')
    parser.add_argument('--source', type=Path, help='Existing local upstream skill directory')
    parser.add_argument('--project', type=Path, help='Project to inspect (no recursive disk scan)')
    parser.add_argument('--target', action='append', default=[], metavar='NAME=PATH',
                        help='Explicit named destination; repeat for multiple destinations')
    parser.add_argument('--state', type=Path, help='Existing receipt directory, or a new private directory')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Print configuration without creating files')
    mode.add_argument('--yes', action='store_true', help='Save explicit --source/--target choices after printing preview')


def path_value(value):
    value = str(value).strip()
    if not value:
        raise ValueError('A path cannot be empty')
    path = Path(value).expanduser().absolute()
    # Do not silently turn a selected alias into a different installation.
    if any(is_link(part) for part in (path, *path.parents)):
        raise ValueError('Choose a real path, not a link: ' + str(path))
    return path.resolve()


def candidates(home, project):
    rows, seen = [], set()
    locations = [('Codex (legacy user)', home/'.codex/skills')]
    for agent, spec in AGENTS.items():
        locations.extend([(agent + ' (user)', home/spec['user']),
                          (agent + ' (project)', project/spec['project'])])
    for label, path in locations:
        key = os.path.normcase(str(path.absolute()))
        if key in seen:
            continue
        seen.add(key)
        try:
            path = path_value(path)
            if path.exists() and not path.is_dir():
                continue
            count = sum(1 for child in path.iterdir()
                        if child.is_dir() and not is_link(child) and (child/'SKILL.md').is_file()) if path.exists() else 0
            rows.append(dict(label=label, path=path, exists=path.exists(), count=count))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda row: not row['exists'])


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def make_config(source, targets, state, config_path):
    source, state, config_path = map(path_value, (source, state, config_path))
    if not source.is_dir():
        raise ValueError('Source directory does not exist: ' + str(source))
    if state.exists() and not state.is_dir():
        raise ValueError('State must be a directory')
    if not targets:
        raise ValueError('Choose at least one target')
    normalized = {}
    for label, value in targets:
        label = label.strip()
        if not label or label in normalized:
            raise ValueError('Target names must be nonempty and unique')
        target = path_value(value)
        if target.exists() and not target.is_dir():
            raise ValueError('Target must be a directory')
        if any(overlaps(target, other) for other in (source, state, config_path, *normalized.values())):
            raise ValueError('Source, targets, state and config must be separate')
        # Validate the existing catalog/registry through the same read-only core.
        Manager(source, target, state)
        normalized[label] = target
    if overlaps(state, source) or overlaps(config_path, source) or overlaps(state, config_path):
        raise ValueError('Keep config/state separate from skills and each other')
    result = dict(schema_version=1, repo=str(REPO), source=str(source), state=str(state),
                  targets={label: str(path) for label, path in normalized.items()})
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 60000:
        raise ValueError('Configuration is too large')
    return result


def quote(value):
    return "'" + str(value).replace("'", "''") + "'" if os.name == 'nt' else shlex.quote(str(value))


def next_steps(config_path, config):
    command = 'python -X utf8 skill_manage.py --config ' + quote(config_path)
    print('\nNext (run from your skillctl checkout):')
    print('  ' + command + ' locations')
    for label in config['targets']:
        print('  ' + command + ' list --target ' + quote(label))
    print('Configuration is ready; no skills have been installed or adopted.')
    print('To let your agent manage skills, follow README.md or docs/initialization.md.')


def run(args, config_path, *, home=None, ask=None):
    home = Path.home() if home is None else Path(home)
    ask = input if ask is None else ask
    config_path = path_value(config_path)
    if config_path.exists():
        from skill_manage import load_config, Management
        config = load_config(config_path)  # Invalid existing files are never overwritten either.
        print('Already configured; kept unchanged: ' + str(config_path))
        print(json.dumps(Management(config).locations(), ensure_ascii=False, indent=2))
        next_steps(config_path, config)
        return 0
    interactive = not (args.yes or args.dry_run)
    if interactive and ask is input and not sys.stdin.isatty():
        raise ValueError('Use a terminal for guided setup, or pass --source, --target and --dry-run/--yes')
    if not interactive and (args.source is None or not args.target):
        raise ValueError('--dry-run/--yes requires explicit --source and --target; no targets guessed')
    print('skillctl setup')
    print('Choose an upstream source and where your agent reads skills. Preview before saving.')
    print('This only writes configuration. No skills are installed or adopted, and no service is started.')
    try:
        source = args.source
        if source is None:
            print('\n1. Source: your local upstream skills folder. Press Enter to try the bundled examples.')
            source = ask('Source path [' + str(REPO) + ']: ').strip() or REPO
        targets = []
        for entry in args.target:
            if '=' not in entry:
                raise ValueError('Use --target NAME=PATH')
            targets.append(tuple(entry.split('=', 1)))
        if not targets:
            print('\n2. Project: inspect only this project and standard user skill directories.')
            project = args.project or (ask('Project path [' + str(Path.cwd()) + ']: ').strip() or Path.cwd())
            project = path_value(project)
            if not project.is_dir():
                raise ValueError('Project directory does not exist')
            rows = candidates(home, project)
            for number, row in enumerate(rows, 1):
                status = f"existing, {row['count']} direct skills" if row['exists'] else 'not created'
                print(f"  {number}. {row['label']}: {row['path']} ({status})")
            print('  0. Custom path')
            print('Existing folders are suggestions, not proof the agent loads them.')
            while True:
                selection = ask('Choose numbers, comma separated; q to cancel: ').strip()
                if selection.lower() == 'q' or not selection:
                    print('Cancelled; no files written.')
                    return 0
                if selection == '0':
                    targets = [(ask('Target name: '), ask('Target directory: '))]
                    break
                try:
                    numbers = list(dict.fromkeys(int(n.strip()) for n in selection.split(',')))
                    if not all(1 <= n <= len(rows) for n in numbers):
                        raise ValueError()
                    targets = [(rows[n-1]['label'], rows[n-1]['path']) for n in numbers]
                    break
                except ValueError:
                    print('Choose a listed number or 0.')
        state = args.state
        if state is None:
            legacy = home/'.codex/skill-manager-state'
            default = legacy if legacy.is_dir() else config_path.parent/'state'
            if interactive:
                print('\n3. Receipts and backups: if you have used skillctl before, reuse its state directory to retain your history.')
                state = ask('State path [' + str(default) + ']: ').strip() or default
            else:
                state = default
        config = make_config(source, targets, state, config_path)
        print('\nPreview: ' + str(config_path))
        print(json.dumps(config, ensure_ascii=False, indent=2))
        print('Only the config file will be created. Skills and receipts stay unchanged.')
        if args.dry_run:
            print('Preview only; no files written.')
            return 0
        if not args.yes and ask('Save configuration? [y/N]: ').strip().lower() not in ('y', 'yes', '是'):
            print('Cancelled; no files written.')
            return 0
        # Revalidate after the user has reviewed the proposal. Exclusive creation
        # prevents another setup process or existing configuration being replaced.
        make_config(config['source'], list(config['targets'].items()), config['state'], config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        print('Saved: ' + str(config_path))
        next_steps(config_path, config)
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled; no configuration saved.')
        return 130
