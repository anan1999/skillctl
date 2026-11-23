#!/usr/bin/env python3
"""Manage only catalogued personal skills. Python 3.9+, standard library only."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


AGENTS = {
    'pi': {'project': '.pi/skills', 'user': '.pi/agent/skills', 'invoke': '/skill:'},
    'codex': {'project': '.agents/skills', 'user': '.agents/skills', 'invoke': '$'},
    'claude-code': {'project': '.claude/skills', 'user': '.claude/skills', 'invoke': '/'},
}


def resolve_target(args):
    if args.target is not None:
        if args.agent or args.scope or args.project:
            raise ValueError('--target cannot be combined with --agent, --scope or --project')
        return args.target
    if not args.agent:
        if args.scope or args.project:
            raise ValueError('--scope and --project require --agent')
        return Path.home() / '.codex/skills'
    scope = args.scope or 'project'
    if scope == 'user':
        if args.project:
            raise ValueError('--project only applies to project scope')
        base = Path.home()
    else:
        base = (args.project or Path.cwd()).expanduser().resolve()
        if not base.is_dir():
            raise ValueError('Project directory does not exist: ' + str(base))
    return base / AGENTS[args.agent][scope]


def comparison_roots(agent, project):
    """Bound duplicate checks to standard user/project locations."""
    if not agent:
        return []
    home = Path.home()
    roots = [home / AGENTS[agent]['user']]
    if agent == 'pi':
        roots.append(home / '.agents/skills')
    if agent == 'codex':
        roots.append(home / '.codex/skills')  # legacy installations
    folder = project.expanduser().resolve()
    for base in (folder, *folder.parents):
        roots.append(base / AGENTS[agent]['project'])
        if agent == 'pi':
            roots.append(base / '.agents/skills')
        if (base / '.git').exists():
            break
    return roots


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def fingerprint(path):
    if path.is_symlink():
        raise ValueError('Symlink in skill: ' + str(path))
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Not an ordinary skill directory: ' + str(path))
    result = {}
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise ValueError('Symlink in skill: ' + str(item))
        relative = item.relative_to(path)
        # Running a bundled Python helper may create bytecode. It is not
        # authored skill content and must not cause false local-drift conflicts.
        if '__pycache__' in relative.parts or item.suffix in ('.pyc', '.pyo'):
            continue
        if item.is_file():
            result[relative.as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def source_revision(source):
    def git(*args):
        p = subprocess.run(['git', '-C', str(source), *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}


def prose_links(text):
    """Exclude fenced examples/templates from authored-document link checks."""
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= fence[1] and not marker[2].strip()):
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            continue
        yield from re.findall(r'\]\(([^)]+)\)', line)


def remove_owned(path, parent):
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError('Refusing removal outside the checked parent: ' + str(path))
    if path.exists():
        shutil.rmtree(path)


def replace_directory(destination, source):
    """Stage beside destination, compensate a failed rename, never merge files."""
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.resolve().parent != parent.resolve():
        raise ValueError('Unsafe destination')
    temp = Path(tempfile.mkdtemp(prefix='.' + destination.name + '-stage-', dir=parent))
    old = parent / ('.' + destination.name + '-old-' + uuid.uuid4().hex)
    moved = False
    try:
        if source is not None:
            fingerprint(source)  # reject links before copying
            shutil.copytree(source, temp, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        if destination.exists():
            os.replace(destination, old)
            moved = True
        try:
            if source is not None:
                os.replace(temp, destination)
        except BaseException:
            if moved:
                os.replace(old, destination)
                moved = False
            raise
        if moved:
            remove_owned(old, parent)
    finally:
        remove_owned(temp, parent)


class Manager:
    def __init__(self, source, target, state):
        self.source = Path(source).resolve()
        target = Path(target).expanduser()
        if target.is_symlink():
            raise ValueError('Select a real target directory, not a symlink')
        self.target = target.resolve()
        key = hashlib.sha256(os.path.normcase(str(self.target)).encode()).hexdigest()[:20]
        self.state = Path(state).expanduser().resolve() / key
        self.db_path = self.state / 'registry.json'
        catalog = read_json(self.source / 'catalog.json')
        self.names = catalog['skills']
        if catalog.get('schema') != 1 or len(set(self.names)) != len(self.names):
            raise ValueError('Invalid catalog')
        if any(not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', n) for n in self.names):
            raise ValueError('Invalid skill name')

    def db(self):
        result = read_json(self.db_path) if self.db_path.exists() else {
            'schema': 1, 'target': str(self.target), 'installed': {}, 'history': {}}
        if result['target'] != str(self.target):
            raise ValueError('Registry target mismatch')
        return result

    @contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / 'operation.lock'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError('Operation lock exists; inspect running work before removing a stale lock')
        try:
            with os.fdopen(fd, 'w') as out:
                out.write(str(os.getpid()))
            yield
        finally:
            path.unlink()

    def validate(self, name):
        if name not in self.names:
            raise ValueError('Not in managed catalog: ' + name)
        folder = self.source / 'skills' / name
        files = fingerprint(folder)
        if not files or 'SKILL.md' not in files:
            raise ValueError('Missing SKILL.md: ' + name)
        text = (folder / 'SKILL.md').read_text(encoding='utf-8-sig')
        match = re.match(r'^---\s*\n(.*?)\n---(?:\s*\n|$)', text, re.S)
        if not match:
            raise ValueError('Missing frontmatter: ' + name)
        fields = dict(re.findall(r'^(name|description):\s*(.+)$', match[1], re.M))
        if fields.get('name') != name or not fields.get('description', '').strip():
            raise ValueError('Invalid name/description: ' + name)
        # Intentionally a basic contract check, not a general YAML parser.
        for doc in folder.rglob('*.md'):
            for link in prose_links(doc.read_text(encoding='utf-8')):
                if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', link) or link.startswith(('/', '#')):
                    continue
                relative = link.split('#', 1)[0]
                resolved = (doc.parent / relative).resolve()
                if not resolved.exists():
                    raise ValueError('Broken local link: ' + str(doc) + ' -> ' + link)
        return files

    def inspect(self, name, db):
        source = self.validate(name)
        current = fingerprint(self.target / name)
        receipt = db['installed'].get(name)
        status = ('missing' if current is None and receipt is None else
                  'unmanaged-identical' if receipt is None and current == source else
                  'unmanaged-conflict' if receipt is None else
                  'local-drift' if current != receipt['files'] else
                  'current' if current == source else 'update-available')
        return {'name': name, 'status': status,
                'installed_commit': receipt.get('source_revision', {}).get('commit') if receipt else None,
                'rollback_depth': len(db['history'].get(name, []))}

    def listing(self):
        db = self.db()
        return [self.inspect(name, db) for name in self.names]

    def doctor(self, roots):
        advice = {
            'current': 'Installed bytes match the source and receipt.',
            'missing': 'Install this skill into the selected target.',
            'update-available': 'Review diff, then install to apply the source update.',
            'local-drift': 'Preserve local edits and reconcile them with the source before installing.',
            'unmanaged-identical': 'Use install --adopt-identical to enroll this existing copy.',
            'unmanaged-conflict': 'Preserve this copy and reconcile it with the source before adopting.',
        }
        db = self.db()
        rows = []
        for name in self.names:
            try:
                row = self.inspect(name, db)
                row['advice'] = advice[row['status']]
            except (ValueError, OSError, KeyError) as error:
                row = {'name': name, 'status': 'invalid', 'error': str(error),
                       'advice': 'Fix the reported source or target file, then rerun doctor.'}
            copies = []
            seen = set()
            for root in [self.target, *roots]:
                path = root / name
                identity = os.path.normcase(str(path.resolve()))
                if identity in seen:
                    continue
                seen.add(identity)
                if not path.exists() and not path.is_symlink():
                    continue
                try:
                    copies.append({'path': str(path), 'files': fingerprint(path)})
                except (ValueError, OSError) as error:
                    copies.append({'path': str(path), 'error': str(error)})
            row['copies'] = copies
            if len(copies) > 1:
                row['duplicate_content_equal'] = all('files' in copy for copy in copies) and all(
                    copy['files'] == copies[0]['files'] for copy in copies[1:])
                row['duplicate_advice'] = 'Review these same-folder-name copies for shadowing; agent settings determine which loads.'
            rows.append(row)
        lock_exists = (self.state / 'operation.lock').exists()
        return {
            'ok': all(row['status'] in ('current', 'update-available') for row in rows)
                  and all('error' not in copy for row in rows for copy in row['copies'])
                  and not lock_exists,
            'operation_lock': str(self.state / 'operation.lock') if lock_exists else None,
            'lock_advice': 'Check for an active operation before handling a stale lock.' if lock_exists else None,
            'skills': rows,
            'scope': 'Catalog skills in standard locations only. Custom paths, plugins, runtime loading and tool dependencies are not verified.',
        }

    def diff(self, name):
        source = self.validate(name); current = fingerprint(self.target / name) or {}
        return {'name': name, 'added': sorted(set(source) - set(current)),
                'removed': sorted(set(current) - set(source)),
                'changed': sorted(k for k in source.keys() & current.keys() if source[k] != current[k])}

    def install(self, name, adopt=False):
        with self.lock():
            desired = self.validate(name); destination = self.target / name
            current = fingerprint(destination); db = self.db(); prior = db['installed'].get(name)
            if prior is not None and current != prior['files']:
                raise ValueError('Local drift; preserve/reconcile edits before installing: ' + name)
            if current is not None and prior is None and not (adopt and current == desired):
                raise ValueError('Unmanaged installation; only --adopt-identical can enroll identical bytes: ' + name)
            if prior is not None and current == desired:
                return {'name': name, 'action': 'unchanged'}
            transaction = self.state / 'snapshots' / uuid.uuid4().hex
            transaction.mkdir(parents=True)
            backup = transaction / 'skill'
            if current is not None:
                shutil.copytree(destination, backup)
            record = {'backup': str(backup) if current is not None else None,
                      'previous_files': current, 'previous_receipt': prior, 'installed_files': desired}
            write_json(transaction / 'transaction.json', record)
            replace_directory(destination, self.source / 'skills' / name)
            try:
                if fingerprint(destination) != desired:
                    raise ValueError('Installed hash verification failed')
                db['installed'][name] = {'files': desired, 'source': str(self.source),
                    'source_revision': source_revision(self.source),
                    'installed_at': datetime.now(timezone.utc).isoformat()}
                db['history'].setdefault(name, []).append(record)
                write_json(self.db_path, db)
            except BaseException:
                replace_directory(destination, backup if current is not None else None)
                raise
            return {'name': name, 'action': 'adopted' if prior is None and current is not None else 'installed'}

    def rollback(self, name):
        if name not in self.names:
            raise ValueError('Not in managed catalog: ' + name)
        with self.lock():
            db = self.db(); history = db['history'].get(name, [])
            if not history:
                raise ValueError('No rollback snapshot: ' + name)
            record = history[-1]; destination = self.target / name
            current = fingerprint(destination)
            if current != record['installed_files']:
                raise ValueError('Local drift; rollback would overwrite local edits')
            backup = Path(record['backup']) if record['backup'] else None
            if backup is not None:
                if self.state not in backup.resolve().parents or fingerprint(backup) != record['previous_files']:
                    raise ValueError('Invalid or modified rollback snapshot')
            # Compensation snapshot if registry persistence fails after replacement.
            compensation = self.state / 'snapshots' / uuid.uuid4().hex
            shutil.copytree(destination, compensation)
            replace_directory(destination, backup)
            try:
                if record['previous_receipt'] is None:
                    db['installed'].pop(name, None)
                else:
                    db['installed'][name] = record['previous_receipt']
                history.pop()
                write_json(self.db_path, db)
            except BaseException:
                replace_directory(destination, compensation)
                raise
            return {'name': name, 'action': 'rolled-back'}


def main():
    # Accept target selection before or after the subcommand.
    selection = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    selection.add_argument('--target', type=Path)
    selection.add_argument('--agent', choices=AGENTS)
    selection.add_argument('--scope', choices=('project', 'user'))
    selection.add_argument('--project', type=Path, help='Project directory (default: current directory)')
    p = argparse.ArgumentParser(description=__doc__, parents=[selection])
    p.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    p.add_argument('--state', type=Path, default=Path.home()/'.codex/skill-manager-state')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('list', parents=[selection])
    sub.add_parser('doctor', parents=[selection])
    check = sub.add_parser('check', parents=[selection]); check.add_argument('--also-target', action='append', type=Path, default=[])
    diff = sub.add_parser('diff', parents=[selection]); diff.add_argument('names', nargs='*')
    install = sub.add_parser('install', parents=[selection]); install.add_argument('names', nargs='*')
    install.add_argument('--all', action='store_true'); install.add_argument('--adopt-identical', action='store_true')
    install.add_argument('--dry-run', action='store_true', help='Preview target and changes without writing')
    roll = sub.add_parser('rollback', parents=[selection]); roll.add_argument('name')
    args = p.parse_args()
    for option in ('target', 'agent', 'scope', 'project'):
        if not hasattr(args, option):
            setattr(args, option, None)
    try:
        m = Manager(args.source, resolve_target(args), args.state)
        if args.command == 'list': result = m.listing()
        elif args.command == 'doctor':
            roots = comparison_roots(args.agent, args.project or Path.cwd())
            result = m.doctor(roots)
            result.update({'target': str(m.target), 'agent': args.agent,
                           'scope_name': (args.scope or 'project') if args.agent else 'custom'})
            if args.agent:
                result['invoke'] = AGENTS[args.agent]['invoke'] + '<skill-name>'
        elif args.command == 'diff': result = [m.diff(n) for n in (args.names or m.names)]
        elif args.command == 'check':
            rows = m.listing(); duplicates = []
            for target in args.also_target:
                for name in m.names:
                    first = fingerprint(m.target/name); second = fingerprint(target/name)
                    if first is not None and second is not None:
                        duplicates.append({'name':name,'other':str(target/name),'identical':first==second})
            ok = not any(r['status'] in ('local-drift','unmanaged-conflict') for r in rows) and all(d['identical'] for d in duplicates)
            result = {'ok':ok,'skills':rows,'duplicate_copies':duplicates,
                      'scope':'Basic structure, local links and hashes; not behavioral validation.'}
        elif args.command == 'install':
            if bool(args.names) == args.all: p.error('Choose skill names OR --all')
            names = m.names if args.all else args.names
            for name in names: m.validate(name)
            if args.dry_run:
                db = m.db()
                plan = []
                lock_exists = (m.state / 'operation.lock').exists()
                for name in names:
                    row = m.inspect(name, db)
                    row['blocked'] = lock_exists or row['status'] in ('local-drift', 'unmanaged-conflict') or (
                        row['status'] == 'unmanaged-identical' and not args.adopt_identical)
                    row['diff'] = m.diff(name)
                    plan.append(row)
                result = {'ok': not any(row['blocked'] for row in plan),
                          'target': str(m.target), 'dry_run': True, 'skills': plan,
                          'operation_lock': str(m.state / 'operation.lock') if lock_exists else None}
            else:
                result = [m.install(name, args.adopt_identical) for name in names]
            if args.agent and not args.dry_run:
                result = {'target': str(m.target), 'agent': args.agent,
                          'scope': args.scope or 'project', 'skills': result,
                          'invoke': [AGENTS[args.agent]['invoke'] + name for name in names]}
        else: result = m.rollback(args.name)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result, dict) and result.get('ok') is False else 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
