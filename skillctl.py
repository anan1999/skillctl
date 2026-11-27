#!/usr/bin/env python3
"""Manage existing and Git-backed skills. Python 3.9+, standard library and Git."""
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
from skillctl_content import discover, is_link, merge_trees, text_diff, valid_name


AGENTS = {
    'pi': {'project': '.pi/skills', 'user': '.pi/agent/skills', 'invoke': '/skill:'},
    'codex': {'project': '.agents/skills', 'user': '.agents/skills', 'invoke': '$'},
    'claude-code': {'project': '.claude/skills', 'user': '.claude/skills', 'invoke': '/'},
    'cursor': {'project': '.cursor/skills', 'user': '.cursor/skills', 'invoke': '/'},
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
    if agent in ('pi', 'cursor'):
        roots.append(home / '.agents/skills')
    if agent == 'codex':
        roots.append(home / '.codex/skills')  # legacy installations
    folder = project.expanduser().resolve()
    for base in (folder, *folder.parents):
        roots.append(base / AGENTS[agent]['project'])
        if agent in ('pi', 'cursor'):
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
    if is_link(path):
        raise ValueError('Symlink in skill: ' + str(path))
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Not an ordinary skill directory: ' + str(path))
    result = {}
    for item in sorted(path.rglob('*')):
        if is_link(item):
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
        if not self.source.is_dir():
            raise ValueError('Source directory does not exist: ' + str(self.source))
        target = Path(target).expanduser()
        if is_link(target):
            raise ValueError('Select a real target directory, not a symlink')
        self.target = target.resolve()
        key = hashlib.sha256(os.path.normcase(str(self.target)).encode()).hexdigest()[:20]
        self.state = Path(state).expanduser().resolve() / key
        self.db_path = self.state / 'registry.json'
        self.sources = {}
        if (self.source / 'catalog.json').exists():
            catalog = read_json(self.source / 'catalog.json')
            names = catalog['skills']
            if catalog.get('schema') != 1 or len(set(names)) != len(names):
                raise ValueError('Invalid catalog')
            if any(not valid_name(n) for n in names):
                raise ValueError('Invalid skill name')
            self.sources = {n: self.source / 'skills' / n for n in names}
        else:
            for folder in discover(self.source):
                if folder.name in self.sources:
                    raise ValueError('Duplicate source skill name: ' + folder.name)
                if not valid_name(folder.name):
                    raise ValueError('Invalid skill name: ' + folder.name)
                self.sources[folder.name] = folder
        self.names = list(self.sources)
        self.names.extend(n for n in self.db()['installed'] if n not in self.sources)
        if any(not valid_name(n) for n in self.names):
            raise ValueError('Invalid registry skill name')

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
        if name not in self.sources:
            raise ValueError('No upstream source for skill; select --source: ' + name)
        return self.validate_folder(self.sources[name], name)

    def validate_folder(self, folder, name):
        if not valid_name(name):
            raise ValueError('Invalid skill name: ' + name)
        files = fingerprint(folder)
        if not files or 'SKILL.md' not in files:
            raise ValueError('Missing SKILL.md: ' + name)
        text = (folder / 'SKILL.md').read_text(encoding='utf-8-sig')
        match = re.match(r'^---\s*\n(.*?)\n---(?:\s*\n|$)', text, re.S)
        if not match:
            raise ValueError('Missing frontmatter: ' + name)
        fields = dict(re.findall(r'^(name|description):\s*(.+)$', match[1], re.M))
        if fields.get('name', '').strip('\"\'') != name or not fields.get('description', '').strip():
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
        source = self.validate(name) if name in self.sources else None
        current = fingerprint(self.target / name)
        receipt = db['installed'].get(name)
        status = ('missing' if current is None and receipt is None else
                  'unmanaged-identical' if receipt is None and current == source else
                  'unmanaged-conflict' if receipt is None else
                  'local-drift' if current != receipt['files'] else
                  'local-only' if source is None else
                  'source-unbound' if receipt.get('source') is None else
                  'customized' if receipt.get('upstream_files') is not None and current != receipt['upstream_files'] else
                  'current' if current == source else 'update-available')
        return {'name': name, 'status': status,
                'installed_commit': receipt.get('source_revision', {}).get('commit') if receipt else None,
                'rollback_depth': len(db['history'].get(name, []))}

    def listing(self):
        db = self.db()
        return [self.inspect(name, db) for name in self.names]

    def doctor(self, roots, names=None):
        advice = {
            'current': 'Installed bytes match the source and receipt.',
            'missing': 'Install this skill into the selected target.',
            'update-available': 'Review diff, then install to apply the source update.',
            'local-drift': 'Preserve local edits and reconcile them with the source before installing.',
            'unmanaged-identical': 'Use install --adopt-identical to enroll this existing copy.',
            'unmanaged-conflict': 'Preserve this copy and reconcile it with the source before adopting.',
            'unmanaged-local': 'Review this local skill and adopt it to start tracking changes.',
            'local-only': 'Managed locally; select an upstream --source to compare or reconcile.',
            'customized': 'Local customizations are preserved. Use diff --text, then reconcile --strategy keep or merge.',
            'source-unbound': 'This locally adopted skill has no upstream baseline. Review diff and use reconcile --strategy keep to bind the selected source.',
        }
        db = self.db()
        rows = []
        for name in (self.names if names is None else names):
            try:
                if not valid_name(name):
                    raise ValueError('Invalid skill name: ' + name)
                row = (self.inspect(name, db) if name in self.sources or name in db['installed']
                       else {'name': name, 'status': 'unmanaged-local' if (self.target / name).exists() else 'missing'})
                row['advice'] = advice[row['status']]
                if (self.target / name).exists():
                    try:
                        self.validate_folder(self.target / name, name)
                    except (ValueError, OSError) as error:
                        row['error'] = str(error)
                        # Keep drift as the primary status for CLI compatibility:
                        # the unreviewed edits must still be preserved.
                        if row['status'] != 'local-drift':
                            row.update(status='invalid', advice='Fix the reported local structure, then rerun doctor.')
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
            'ok': all(row['status'] in ('current', 'update-available', 'customized', 'local-only') for row in rows)
                  and all('error' not in copy for row in rows for copy in row['copies'])
                  and not lock_exists,
            'operation_lock': str(self.state / 'operation.lock') if lock_exists else None,
            'lock_advice': 'Check for an active operation before handling a stale lock.' if lock_exists else None,
            'skills': rows,
            'scope': 'Source and adopted skills in standard locations only. Runtime loading and tool dependencies are not verified. Use scan for unmanaged skills.',
        }

    def diff(self, name, include_text=False):
        source = self.validate(name); current = fingerprint(self.target / name) or {}
        result = {'name': name, 'added': sorted(set(source) - set(current)),
                'removed': sorted(set(current) - set(source)),
                'changed': sorted(k for k in source.keys() & current.keys() if source[k] != current[k])}
        if include_text:
            db = self.db()
            prior = db['installed'].get(name)
            base = self.baseline(name, prior, required=False)
            local = self.contents(self.target / name)
            upstream = self.contents(self.sources[name])
            result['status'] = self.inspect(name, db)['status']
            result['blocked_reason'] = self.blocked_reason(name, db)
            result['baseline_available'] = base is not None
            result['files'] = []
            for path in sorted(set(local) | set(upstream) | set(base or {})):
                b, l, u = (base or {}).get(path), local.get(path), upstream.get(path)
                if l == u and (base is None or b == l):
                    continue
                row = {'file': path, 'local_vs_upstream': text_diff(l, u, 'local/' + path, 'upstream/' + path)}
                if base is not None:
                    row['local_changed'] = l != b
                    row['upstream_changed'] = u != b
                    row['base_to_local'] = text_diff(b, l, 'base/' + path, 'local/' + path)
                    row['base_to_upstream'] = text_diff(b, u, 'base/' + path, 'upstream/' + path)
                result['files'].append(row)
        return result

    def blocked_reason(self, name, db):
        status = self.inspect(name, db)['status']
        return {
            'local-drift': 'Local files changed since the receipt. Installing would overwrite unreviewed edits.',
            'customized': 'This installation includes preserved local customizations. Reconcile instead of overwriting.',
            'unmanaged-conflict': 'An existing skill differs from upstream and has no receipt. Review diff, then adopt it.',
            'unmanaged-identical': 'The existing skill has no receipt. Adopt it or use install --adopt-identical.',
            'local-only': 'No upstream source is selected for this locally managed skill.',
            'source-unbound': 'This locally adopted skill has no upstream baseline. Review diff and reconcile before installing.',
        }.get(status)

    @staticmethod
    def contents(folder):
        files = fingerprint(folder) or {}
        return {name: (folder / name).read_bytes() for name in files}

    def baseline(self, name, receipt, required=True):
        if receipt and receipt.get('baseline'):
            folder = Path(receipt['baseline'])
            if self.state not in folder.resolve().parents or fingerprint(folder) != receipt['upstream_files']:
                raise ValueError('Invalid or modified merge baseline')
            return self.contents(folder)
        # Receipts from older releases have hashes but not historical contents.
        if receipt and name in self.sources and self.validate(name) == receipt['files']:
            return self.contents(self.sources[name])
        if required:
            raise ValueError('No verified common baseline. Review diff and use keep to establish one; automatic merge is unavailable.')
        return None

    def scan(self):
        db = self.db()
        rows = []
        for folder in discover(self.target):
            name = folder.name
            row = {'name': name, 'path': str(folder), 'target': str(self.target),
                   'adoptable': folder.parent == self.target}
            try:
                files = self.validate_folder(folder, name)
                row['files'] = files
                row['status'] = (self.inspect(name, db)['status'] if name in self.sources or name in db['installed']
                                 else 'unmanaged-local') if row['adoptable'] else 'nested'
                row['source'] = str(self.sources[name]) if name in self.sources else None
                if not row['adoptable']:
                    row['advice'] = 'Select the containing directory with --target to adopt this nested skill in place.'
            except (ValueError, OSError) as error:
                row.update(status='invalid', error=str(error), adoptable=False)
            rows.append(row)
        # Report linked directories explicitly, without following them.
        if self.target.is_dir():
            for folder in self.target.iterdir():
                if is_link(folder):
                    rows.append({'name': folder.name, 'path': str(folder), 'target': str(self.target),
                                 'status': 'linked', 'adoptable': False,
                                 'advice': 'Linked skills are not modified. Select their real containing directory explicitly.'})
        return rows

    def record(self, name, prepared, upstream, replace=True, origin=None):
        """Caller holds the operation lock. Persist baseline and rollback atomically with receipt."""
        destination = self.target / name
        current = fingerprint(destination)
        desired = fingerprint(prepared)
        db = self.db()
        prior = db['installed'].get(name)
        transaction = self.state / 'snapshots' / uuid.uuid4().hex
        transaction.mkdir(parents=True)
        backup = transaction / 'skill'
        if current is not None:
            shutil.copytree(destination, backup)
        base = transaction / 'upstream'
        upstream_files = fingerprint(upstream) if upstream is not None else None
        if upstream is not None:
            shutil.copytree(upstream, base)
            if fingerprint(base) != upstream_files:
                raise ValueError('Upstream changed while snapshotting')
        record = {'backup': str(backup) if current is not None else None,
                  'previous_files': current, 'previous_receipt': prior, 'installed_files': desired}
        write_json(transaction / 'transaction.json', record)
        if replace:
            replace_directory(destination, prepared)
        try:
            if fingerprint(destination) != desired:
                raise ValueError('Installed hash verification failed')
            db['installed'][name] = {'files': desired,
                'source': (('lockfile:' + origin['lockfile_sha256']) if origin else str(self.source)) if upstream is not None else None,
                'source_revision': origin['source_revision'] if origin else (source_revision(self.source) if upstream is not None else {}),
                'upstream_files': upstream_files, 'baseline': str(base) if upstream is not None else None,
                'installed_at': datetime.now(timezone.utc).isoformat()}
            if origin:
                db['installed'][name]['lockfile_sha256'] = origin['lockfile_sha256']
            db['history'].setdefault(name, []).append(record)
            write_json(self.db_path, db)
        except BaseException:
            if replace:
                replace_directory(destination, backup if current is not None else None)
            raise
        if name not in self.names:
            self.names.append(name)

    def preview_stamp(self, name, action=None):
        """Bind an interactive preview to contents and receipts, not just a filename."""
        if not valid_name(name):
            raise ValueError('Invalid skill name')
        db = self.db()
        if action == 'rollback':
            history = db['history'].get(name, [])
            record = history[-1] if history else {}
            backup = Path(record['backup']) if record.get('backup') else None
            if backup is not None and self.state not in backup.resolve().parents:
                raise ValueError('Invalid rollback snapshot path')
            payload = {'target': str(self.target), 'registry': db,
                       'local': fingerprint(self.target / name),
                       'backup': fingerprint(backup) if backup else None}
            return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        prior = db['installed'].get(name)
        base = self.baseline(name, prior, required=False) if prior else None
        payload = {'source': str(self.source), 'target': str(self.target), 'registry': db,
                   'local': fingerprint(self.target / name),
                   'upstream': self.validate(name) if name in self.sources else None,
                   'base': {key: hashlib.sha256(value).hexdigest() for key, value in (base or {}).items()}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def verify_preview(self, name, expected, action=None):
        if expected is not None and self.preview_stamp(name, action) != expected:
            raise ValueError('Preview is stale; files or receipts changed. Preview again before applying.')

    def adopt(self, name, apply=False, expected=None):
        def operation():
            self.verify_preview(name, expected)
            destination = self.target / name
            files = self.validate_folder(destination, name)
            prior = self.db()['installed'].get(name)
            if prior:
                raise ValueError('Already managed; use reconcile for local edits: ' + name)
            upstream = self.sources.get(name)
            upstream_files = self.validate(name) if upstream else None
            result = {'name': name, 'action': 'adopted' if apply else 'would-adopt',
                      'files': files, 'upstream': str(upstream) if upstream else None,
                      'customized': upstream_files is not None and files != upstream_files,
                      'baseline_note': 'Selected upstream becomes the comparison baseline at adoption; earlier history is unknown.',
                      'target_unchanged': True}
            if apply:
                self.verify_preview(name, expected)
                self.record(name, destination, upstream, replace=False)
            return result
        if not apply:
            return operation()
        with self.lock():
            return operation()

    def reconcile(self, name, strategy, apply=False, resolved=None, expected=None):
        def operation():
            self.verify_preview(name, expected)
            self.validate(name)
            destination = self.target / name
            self.validate_folder(destination, name)
            prior = self.db()['installed'].get(name)
            if not prior:
                raise ValueError('Adopt the existing skill before reconciling: ' + name)
            local = self.contents(destination)
            upstream = self.contents(self.sources[name])
            if strategy == 'keep':
                merged, conflicts = local, []
            elif resolved:
                if not resolved.is_dir():
                    raise ValueError('Resolved skill directory does not exist')
                self.validate_folder(resolved, name)
                merged, conflicts = self.contents(resolved), []
                if any(re.search(rb'(?m)^(<<<<<<<|\|\|\|\|\|\|\||=======|>>>>>>>)', b) for b in merged.values()):
                    raise ValueError('Unresolved conflict markers in resolved directory')
            else:
                merged, conflicts = merge_trees(self.baseline(name, prior), local, upstream)
            result = {'name': name, 'strategy': strategy, 'ok': not conflicts,
                      'action': 'blocked' if conflicts else 'preview', 'conflicts': conflicts,
                      'changes': [{'file': path, **text_diff(local.get(path), merged.get(path),
                                   'local/' + path, 'result/' + path)}
                                  for path in sorted(local.keys() | merged.keys())
                                  if local.get(path) != merged.get(path)] if not conflicts else [],
                      'note': 'Keep preserves all local bytes and advances the upstream baseline. Merge preserves nonconflicting local edits.'}
            if conflicts:
                return result
            with tempfile.TemporaryDirectory(prefix='skillctl-result-') as temp:
                prepared = Path(temp) / name
                prepared.mkdir()
                for path, data in merged.items():
                    output = prepared / path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
                try:
                    self.validate_folder(prepared, name)
                except ValueError as error:
                    result.update(ok=False, action='blocked', conflicts=[{'file': name, 'reason': str(error), 'preview': None}])
                    return result
                if not apply:
                    return result
                if local != self.contents(destination) or upstream != self.contents(self.sources[name]):
                    raise ValueError('Files changed during reconciliation; rerun preview')
                self.verify_preview(name, expected)
                self.record(name, prepared, self.sources[name], replace=(strategy != 'keep'))
            result['action'] = 'kept' if strategy == 'keep' else 'merged'
            return result
        if not apply:
            return operation()
        with self.lock():
            return operation()

    @staticmethod
    def content_changes(before, after):
        return [{'file': path, 'change': 'added' if path not in before else 'removed' if path not in after else 'modified',
                 'before_sha256': hashlib.sha256(before[path]).hexdigest() if path in before else None,
                 'after_sha256': hashlib.sha256(after[path]).hexdigest() if path in after else None,
                 **text_diff(before.get(path), after.get(path), 'local/' + path, 'result/' + path)}
                for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]

    def install(self, name, adopt=False, apply=True, expected=None):
        def operation():
            self.verify_preview(name, expected)
            desired = self.validate(name); destination = self.target / name
            current = fingerprint(destination); db = self.db(); prior = db['installed'].get(name)
            if prior is not None and current != prior['files']:
                raise ValueError('Local drift; preserve/reconcile edits before installing: ' + name)
            if prior is not None and prior.get('source') is None:
                raise ValueError('Locally adopted skill has no upstream baseline; reconcile before installing: ' + name)
            if prior is not None and prior.get('upstream_files') is not None and current != prior['upstream_files']:
                raise ValueError('Preserved local customizations; use reconcile instead of install: ' + name)
            if current is not None and prior is None and not (adopt and current == desired):
                raise ValueError('Unmanaged installation; only --adopt-identical can enroll identical bytes: ' + name)
            if prior is not None and current == desired:
                return {'name': name, 'action': 'unchanged', 'ok': True, 'changes': []}
            if not apply:
                return {'name': name, 'action': 'would-update' if prior else 'would-install', 'ok': True,
                        'destination': str(destination), 'source': str(self.sources[name]),
                        'before_files': current, 'after_files': desired,
                        'changes': self.content_changes(self.contents(destination), self.contents(self.sources[name]))}
            self.verify_preview(name, expected)
            self.record(name, self.sources[name], self.sources[name])
            return {'name': name, 'action': 'adopted' if prior is None and current is not None else 'installed'}
        if not apply:
            return operation()
        with self.lock():
            return operation()

    def rollback(self, name, apply=True, expected=None):
        if name not in self.names:
            raise ValueError('Not in managed catalog: ' + name)
        def operation():
            self.verify_preview(name, expected, 'rollback')
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
            if not apply:
                return {'name': name, 'action': 'would-rollback', 'ok': True,
                        'destination': str(destination), 'removes_directory': backup is None,
                        'restores_managed': record['previous_receipt'] is not None,
                        'before_files': current, 'after_files': record['previous_files'],
                        'changes': self.content_changes(self.contents(destination), self.contents(backup) if backup else {})}
            self.verify_preview(name, expected, 'rollback')
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
        if not apply:
            return operation()
        with self.lock():
            return operation()


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
    scan = sub.add_parser('scan', parents=[selection], help='Discover existing skills without modifying files')
    scan.add_argument('--all-agents', action='store_true', help='Scan standard targets for every supported harness')
    adopt = sub.add_parser('adopt', parents=[selection], help='Enroll existing skills in place; preview by default')
    adopt.add_argument('names', nargs='+')
    adopt.add_argument('--apply', action='store_true')
    reconcile = sub.add_parser('reconcile', parents=[selection], help='Keep or merge local edits; preview by default')
    reconcile.add_argument('name')
    reconcile.add_argument('--strategy', choices=('keep', 'merge'), required=True)
    reconcile.add_argument('--apply', action='store_true')
    reconcile.add_argument('--resolved', type=Path, help='Explicit manually resolved skill directory (merge only)')
    reconcile.add_argument('--text', action='store_true', help='Print readable patches instead of JSON')
    check = sub.add_parser('check', parents=[selection]); check.add_argument('--also-target', action='append', type=Path, default=[])
    diff = sub.add_parser('diff', parents=[selection]); diff.add_argument('names', nargs='*')
    diff.add_argument('--text', action='store_true', help='Show readable local/base/upstream patches')
    diff.add_argument('--json', action='store_true', help='Include text patches in JSON instead')
    install = sub.add_parser('install', parents=[selection]); install.add_argument('names', nargs='*')
    install.add_argument('--all', action='store_true'); install.add_argument('--adopt-identical', action='store_true')
    install.add_argument('--dry-run', action='store_true', help='Preview target and changes without writing')
    roll = sub.add_parser('rollback', parents=[selection]); roll.add_argument('name')
    args = p.parse_args()
    for option in ('target', 'agent', 'scope', 'project'):
        if not hasattr(args, option):
            setattr(args, option, None)
    try:
        if args.command == 'scan' and args.all_agents:
            if args.agent or args.target:
                raise ValueError('--all-agents cannot be combined with --agent or --target')
            rows, seen = [], set()
            for agent in AGENTS:
                selection_args = argparse.Namespace(**vars(args))
                selection_args.agent = agent
                root = resolve_target(selection_args)
                roots = [root, *comparison_roots(agent, args.project or Path.cwd())]
                user_roots = {Path.home() / spec['user'] for spec in AGENTS.values()} | {Path.home() / '.codex/skills'}
                if args.scope == 'user':
                    roots = [path for path in roots if path in user_roots]
                elif args.scope == 'project':
                    roots = [path for path in roots if path not in user_roots or path == root]
                for root in roots:
                    identity = os.path.normcase(str(root.resolve()))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    rows.extend(Manager(args.source, root, args.state).scan())
            groups = {}
            for row in rows:
                groups.setdefault(row['name'], []).append(row['path'])
            for row in rows:
                row['same_name_copies'] = groups[row['name']]
            print(json.dumps({'skills': rows, 'read_only': True}, ensure_ascii=False, indent=2))
            return 0
        m = Manager(args.source, resolve_target(args), args.state)
        if args.command == 'list': result = m.listing()
        elif args.command == 'scan': result = {'skills': m.scan(), 'read_only': True}
        elif args.command == 'adopt':
            # Preflight all requested skills before the first mutation.
            for name in args.names:
                m.adopt(name)
            result = [m.adopt(name, apply=args.apply) for name in args.names]
        elif args.command == 'reconcile':
            if args.resolved and args.strategy != 'merge':
                raise ValueError('--resolved requires --strategy merge')
            result = m.reconcile(args.name, args.strategy, args.apply, args.resolved)
        elif args.command == 'doctor':
            roots = comparison_roots(args.agent, args.project or Path.cwd())
            result = m.doctor(roots)
            result.update({'target': str(m.target), 'agent': args.agent,
                           'scope_name': (args.scope or 'project') if args.agent else 'custom'})
            if args.agent:
                result['invoke'] = AGENTS[args.agent]['invoke'] + '<skill-name>'
        elif args.command == 'diff': result = [m.diff(n, args.text or args.json) for n in (args.names or m.names)]
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
                    row['blocked'] = lock_exists or row['status'] in ('local-drift', 'unmanaged-conflict', 'customized', 'local-only', 'source-unbound') or (
                        row['status'] == 'unmanaged-identical' and not args.adopt_identical)
                    row['blocked_reason'] = 'Operation lock exists.' if lock_exists else m.blocked_reason(name, db)
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
        if getattr(args, 'text', False) and not getattr(args, 'json', False):
            for row in result if isinstance(result, list) else [result]:
                print(row['name'] + ': ' + row.get('status', row.get('action', '')))
                if row.get('blocked_reason'):
                    print('Blocked: ' + row['blocked_reason'])
                for file in row.get('files', []):
                    print('\nFile: ' + file['file'])
                    for key in ('base_to_local', 'base_to_upstream', 'local_vs_upstream'):
                        if key in file:
                            print(file[key]['diff'] or ('[' + key + ': ' + file[key]['kind'] + ']'))
                for change in row.get('changes', []):
                    print(change['diff'] or ('[' + change['file'] + ': ' + change['kind'] + ']'))
                for conflict in row.get('conflicts', []):
                    print(conflict['file'] + ': ' + conflict['reason'])
                    if conflict.get('preview'):
                        print(conflict['preview'])
        else:
            print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result, dict) and result.get('ok') is False else 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
