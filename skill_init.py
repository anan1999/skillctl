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
        raise ValueError('A path cannot be empty / 路徑不能空白')
    path = Path(value).expanduser().absolute()
    # Do not silently turn a selected alias into a different installation.
    if any(is_link(part) for part in (path, *path.parents)):
        raise ValueError('Choose a real path, not a link / 請選擇實際目錄: ' + str(path))
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
        raise ValueError('Source directory does not exist / 來源目錄不存在: ' + str(source))
    if state.exists() and not state.is_dir():
        raise ValueError('State must be a directory / 收據位置必須是目錄')
    if not targets:
        raise ValueError('Choose at least one target / 請選擇至少一個管理位置')
    normalized = {}
    for label, value in targets:
        label = label.strip()
        if not label or label in normalized:
            raise ValueError('Target names must be nonempty and unique / 名稱不能空白或重複')
        target = path_value(value)
        if target.exists() and not target.is_dir():
            raise ValueError('Target must be a directory / 目標必須是目錄')
        if any(overlaps(target, other) for other in (source, state, config_path, *normalized.values())):
            raise ValueError('Source, targets, state and config must be separate / 來源、目標與管理資料不能重疊')
        # Validate the existing catalog/registry through the same read-only core.
        Manager(source, target, state)
        normalized[label] = target
    if overlaps(state, source) or overlaps(config_path, source) or overlaps(state, config_path):
        raise ValueError('Keep config/state separate from skills and each other / 設定與收據請分開存放')
    result = dict(schema_version=1, repo=str(REPO), source=str(source), state=str(state),
                  targets={label: str(path) for label, path in normalized.items()})
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 60000:
        raise ValueError('Configuration is too large / 設定檔過大')
    return result


def quote(value):
    return "'" + str(value).replace("'", "''") + "'" if os.name == 'nt' else shlex.quote(str(value))


def next_steps(config_path, config):
    command = 'python -X utf8 skill_manage.py --config ' + quote(config_path)
    print('\nNext / 下一步（在 skillctl checkout 執行）:')
    print('  ' + command + ' locations')
    for label in config['targets']:
        print('  ' + command + ' list --target ' + quote(label))
    print('Configuration is ready; no skills installed or adopted / 設定完成；尚未安裝或接管 skills。')
    print('To let your agent manage skills, follow docs/quickstart.zh-TW.md or docs/initialization.md.')


def run(args, config_path, *, home=None, ask=None):
    home = Path.home() if home is None else Path(home)
    ask = input if ask is None else ask
    config_path = path_value(config_path)
    if config_path.exists():
        from skill_manage import load_config, Management
        config = load_config(config_path)  # Invalid existing files are never overwritten either.
        print('Already configured; kept unchanged / 已有設定，保持原樣: ' + str(config_path))
        print(json.dumps(Management(config).locations(), ensure_ascii=False, indent=2))
        next_steps(config_path, config)
        return 0
    interactive = not (args.yes or args.dry_run)
    if interactive and ask is input and not sys.stdin.isatty():
        raise ValueError('Use a terminal for guided setup, or pass --source, --target and --dry-run/--yes / 請在終端機執行引導設定')
    if not interactive and (args.source is None or not args.target):
        raise ValueError('--dry-run/--yes requires explicit --source and --target; no targets guessed')
    print('skillctl setup / 管理 skills 的第一次設定')
    print('Choose an upstream source and where your agent reads skills. Preview before saving.')
    print('選來源與 AI 讀取 skills 的位置；這一步只寫設定，不安裝、不接管、不啟動服務。')
    try:
        source = args.source
        if source is None:
            print('\n1. Source / 來源：上游 skills 的本機副本。先試用可按 Enter 使用內附範例。')
            source = ask('Source path [' + str(REPO) + ']: ').strip() or REPO
        targets = []
        for entry in args.target:
            if '=' not in entry:
                raise ValueError('Use --target NAME=PATH / 目標格式為 名稱=路徑')
            targets.append(tuple(entry.split('=', 1)))
        if not targets:
            print('\n2. Project / 專案：只查看這個專案和使用者的標準 skills 目錄。')
            project = args.project or (ask('Project path [' + str(Path.cwd()) + ']: ').strip() or Path.cwd())
            project = path_value(project)
            if not project.is_dir():
                raise ValueError('Project directory does not exist / 專案目錄不存在')
            rows = candidates(home, project)
            for number, row in enumerate(rows, 1):
                status = f"existing / 已存在, {row['count']} direct skills" if row['exists'] else 'not created / 尚未建立'
                print(f"  {number}. {row['label']}: {row['path']} ({status})")
            print('  0. Custom path / 自訂目錄')
            print('Existing folders are suggestions, not proof the agent loads them / 目錄存在不代表 agent 已載入。')
            while True:
                selection = ask('Choose numbers, comma separated; q to cancel / 輸入編號，逗號分隔: ').strip()
                if selection.lower() == 'q' or not selection:
                    print('Cancelled; no files written / 已取消，未寫入檔案。')
                    return 0
                if selection == '0':
                    targets = [(ask('Target name / 名稱: '), ask('Target directory / 目錄: '))]
                    break
                try:
                    numbers = list(dict.fromkeys(int(n.strip()) for n in selection.split(',')))
                    if not all(1 <= n <= len(rows) for n in numbers):
                        raise ValueError()
                    targets = [(rows[n-1]['label'], rows[n-1]['path']) for n in numbers]
                    break
                except ValueError:
                    print('Choose a listed number or 0 / 請輸入列表編號或 0。')
        state = args.state
        if state is None:
            legacy = home/'.codex/skill-manager-state'
            default = legacy if legacy.is_dir() else config_path.parent/'state'
            if interactive:
                print('\n3. Receipts / 收據與備份：以前用過 skillctl，請沿用原目錄，才能保留管理歷史。')
                state = ask('State path [' + str(default) + ']: ').strip() or default
            else:
                state = default
        config = make_config(source, targets, state, config_path)
        print('\nPreview / 儲存前預覽: ' + str(config_path))
        print(json.dumps(config, ensure_ascii=False, indent=2))
        print('Only the config file will be created / 只建立此設定檔，skills 與收據保持原樣。')
        if args.dry_run:
            print('Preview only; no files written / 僅預覽，未寫入檔案。')
            return 0
        if not args.yes and ask('Save configuration? / 儲存設定？ [y/N]: ').strip().lower() not in ('y', 'yes', '是'):
            print('Cancelled; no files written / 已取消，未寫入檔案。')
            return 0
        # Revalidate after the user has reviewed the proposal. Exclusive creation
        # prevents another setup process or existing configuration being replaced.
        make_config(config['source'], list(config['targets'].items()), config['state'], config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        print('Saved / 已儲存: ' + str(config_path))
        next_steps(config_path, config)
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled; no configuration saved / 已取消，未儲存設定。')
        return 130
