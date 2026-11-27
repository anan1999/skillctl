"""Portable project snapshots, restored on demand through skillctl receipts."""
import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import uuid

from skillctl import fingerprint, replace_directory
from skillctl_content import is_link, valid_name
from skill_quality import parse_json

FORMAT = 'skillctl-project-lock'
MAX_JSON = 32 * 1024 * 1024
MAX_BYTES = 16 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_FILES = 2048
SHA = re.compile(r'[a-f0-9]{64}')


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    path = Path(value).expanduser().absolute()
    if any(is_link(p) for p in (path, *path.parents)):
        raise ValueError('Use an ordinary path, not a link: ' + str(path))
    return path.resolve()


def read_bytes(path, limit=MAX_JSON):
    with safe_path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Lock data exceeds the size limit')
    return data


def revision(value):
    commit, dirty = value.get('commit'), value.get('dirty')
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', commit)):
        raise ValueError('Invalid source commit')
    if dirty is not None and type(dirty) is not bool:
        raise ValueError('Invalid source dirty flag')
    return {'commit': commit, 'dirty': dirty}


def tree_paths(files):
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES or 'SKILL.md' not in files:
        raise ValueError('Expected a bounded file map containing SKILL.md')
    seen, directories, spellings = set(), set(), {}
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
        prefix + suffix for prefix in ('COM', 'LPT') for suffix in '123456789¹²³'}
    for name, sha in files.items():
        if not isinstance(name, str) or len(name) > 240 or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError('Invalid locked file name or hash')
        parts = name.split('/')
        for part in parts:
            if (not part or part in ('.', '..') or part.casefold() in ('.git', '.hg', '.svn', '__pycache__')
                    or part.endswith(('.', ' ')) or re.search(r'[<>:"\\|?*\x00-\x1f\x7f]', part)
                    or part.split('.')[0].rstrip(' ').upper() in reserved or part.endswith(('.pyc', '.pyo'))):
                raise ValueError('File path is not portable: ' + name)
        for i in range(1, len(parts) + 1):
            spelling = '/'.join(parts[:i])
            identity = unicodedata.normalize('NFC', spelling).casefold()
            if identity in spellings and spellings[identity] != spelling:
                raise ValueError('Portable filename collision: ' + name)
            spellings[identity] = spelling
        key = unicodedata.normalize('NFC', name).casefold()
        if key in seen or key in directories:
            raise ValueError('Portable filename collision: ' + name)
        parents = ['/'.join(key.split('/')[:i]) for i in range(1, len(parts))]
        if any(p in seen for p in parents):
            raise ValueError('File/directory collision: ' + name)
        seen.add(key)
        directories.update(parents)


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'schema_version', 'skills', 'objects'}
            or value['format'] != FORMAT or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('Unsupported skill lockfile format')
    skills, objects = value['skills'], value['objects']
    if not isinstance(skills, list) or not 1 <= len(skills) <= 128:
        raise ValueError('A lockfile must contain 1..128 skills')
    if not isinstance(objects, dict) or len(objects) > MAX_FILES * 2:
        raise ValueError('Invalid or oversized object table')
    decoded, size = {}, 0
    for sha, blob in objects.items():
        if not isinstance(sha, str) or not SHA.fullmatch(sha) or not isinstance(blob, dict):
            raise ValueError('Invalid locked object')
        if set(blob) == {'text'} and isinstance(blob['text'], str):
            data = blob['text'].encode('utf-8')
        elif set(blob) == {'base64'} and isinstance(blob['base64'], str):
            try:
                data = base64.b64decode(blob['base64'], validate=True)
            except (ValueError, UnicodeError) as error:
                raise ValueError('Invalid base64 object') from error
        else:
            raise ValueError('Expected a text or base64 object')
        size += len(data)
        if len(data) > MAX_FILE or size > MAX_BYTES:
            raise ValueError('Locked content exceeds the size limit')
        if digest(data) != sha:
            raise ValueError('Locked object hash mismatch')
        decoded[sha] = data
    used, names, expanded_size, expanded_count = set(), set(), 0, 0
    for entry in skills:
        if not isinstance(entry, dict) or set(entry) != {'name', 'files', 'upstream_files', 'source_revision'}:
            raise ValueError('Invalid locked skill entry')
        name = entry['name']
        if not isinstance(name, str) or not valid_name(name) or name in names:
            raise ValueError('Duplicate or invalid locked skill name')
        names.add(name)
        if not isinstance(entry['source_revision'], dict) or entry['source_revision'] != revision(entry['source_revision']):
            raise ValueError('Invalid source revision fields')
        tree_paths(entry['files'])
        for files in (entry['files'], entry['upstream_files']):
            if files is None:
                continue
            tree_paths(files)
            for sha in files.values():
                if sha not in decoded:
                    raise ValueError('Missing locked object')
                expanded_size += len(decoded[sha])
                expanded_count += 1
            if expanded_size > MAX_BYTES or expanded_count > MAX_FILES * 2:
                raise ValueError('Expanded lock content exceeds the size limit')
            used.update(files.values())
    if used != set(decoded):
        raise ValueError('Missing or unreferenced locked objects')
    return decoded


def load(path):
    raw = read_bytes(path)
    value = parse_json(raw.decode('utf-8-sig'))
    return value, validate(value), digest(raw)


def summary(value):
    return [{'name': e['name'], 'files': e['files'], 'source_revision': e['source_revision'],
             'baseline_available': e['upstream_files'] is not None,
             'customized': e['files'] != e['upstream_files'] if e['upstream_files'] is not None else None}
            for e in value['skills']]


def materialize(folder, files, objects):
    folder.mkdir(parents=True)
    for name, sha in files.items():
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(objects[sha])


def stamp(manager, names):
    db = manager.db()
    bases = {}
    for name in names:
        prior = db['installed'].get(name)
        base = manager.baseline(name, prior, required=False) if prior else None
        bases[name] = {p: digest(data) for p, data in base.items()} if base is not None else None
    return digest(encode({'registry': db, 'files': {n: fingerprint(manager.target/n) for n in names}, 'baselines': bases}))


def capture(manager, names):
    db = manager.db()
    names = sorted(names or db['installed'])
    if not names or len(names) != len(set(names)):
        raise ValueError('Choose managed installed skills; adopt existing copies before locking them')
    if any(not valid_name(n) or n not in db['installed'] for n in names):
        raise ValueError('Only managed skills can be locked; adopt unmanaged copies first')
    before = stamp(manager, names)
    objects, skills = {}, []
    def add(contents):
        result = {}
        for path, data in contents.items():
            sha = digest(data)
            try:
                blob = {'text': data.decode('utf-8')}
            except UnicodeDecodeError:
                blob = {'base64': base64.b64encode(data).decode('ascii')}
            objects[sha] = blob
            result[path] = sha
        return result
    for name in names:
        manager.validate_folder(manager.target/name, name)
        prior = db['installed'][name]
        base = manager.baseline(name, prior, required=prior.get('upstream_files') is not None)
        skills.append({'name': name, 'files': add(manager.contents(manager.target/name)),
                       'upstream_files': add(base) if base is not None else None,
                       'source_revision': revision(prior.get('source_revision', {}))})
    value = {'format': FORMAT, 'schema_version': 1, 'skills': skills, 'objects': objects}
    validate(value)
    if len(encode(value)) > MAX_JSON:
        raise ValueError('Lockfile exceeds the size limit')
    if before != stamp(manager, names):
        raise ValueError('Skills or receipts changed while capturing; preview again')
    return value, before


def binding(manager):
    return {key: str(getattr(manager, key)) for key in ('source', 'target', 'state')}


def lock_path(manager, path):
    safe_path(manager.target)
    safe_path(manager.state)
    path = safe_path(path)
    protected = [manager.target, manager.state, *manager.sources.values()]
    if any(path == p or p in path.parents for p in protected):
        raise ValueError('Keep the lockfile outside skill and receipt directories')
    return path


def save_plan(manager, record):
    folder = safe_path(manager.state/'lock-plans')
    folder.mkdir(parents=True, exist_ok=True)
    plan_id = uuid.uuid4().hex
    record.update(schema_version=1, binding=binding(manager), expires_at=time.time()+600)
    data = encode(record)
    if len(data) > MAX_JSON + 65536:
        raise ValueError('Lock plan exceeds the size limit')
    with (folder/(plan_id+'.json')).open('xb') as stream:
        stream.write(data)
    return plan_id


def create_preview(manager, path, names=()):
    path = lock_path(manager, path)
    old, _, previous = load(path) if path.exists() else ({'skills': []}, None, None)
    value, before = capture(manager, names)
    new_sha = digest(encode(value))
    result = {'ok': True, 'action': 'create-lockfile', 'path': str(path), 'target': str(manager.target),
              'previous_sha256': previous, 'sha256': new_sha, 'skills': summary(value), 'plan_id': None,
              'note': 'Contains full skill content and local customizations. Review before sharing. No skills are changed.'}
    old_entries = {e['name']: e for e in old['skills']}
    new_entries = {e['name']: e for e in value['skills']}
    result['changes'] = [{'name': name, 'before': old_entries.get(name), 'after': new_entries.get(name)}
                         for name in sorted(old_entries.keys() | new_entries.keys())
                         if old_entries.get(name) != new_entries.get(name)]
    if previous != new_sha:
        result['plan_id'] = save_plan(manager, {'action': 'create', 'path': str(path), 'previous_sha256': previous,
                                              'value': value, 'stamp': before})
    return result


@contextmanager
def staged(manager, value, objects):
    with tempfile.TemporaryDirectory(prefix='slock-') as temp:
        root = Path(temp)
        for entry in value['skills']:
            name = entry['name']
            for kind, files in (('files', entry['files']), ('base', entry['upstream_files'])):
                if files is not None:
                    folder = root/kind/name
                    materialize(folder, files, objects)
                    manager.validate_folder(folder, name)
                    if fingerprint(folder) != files:
                        raise ValueError('This filesystem cannot reproduce the locked filenames')
        yield root


def restore_rows(manager, value, objects, keep_local=False):
    db, rows = manager.db(), []
    for entry in value['skills']:
        name, desired = entry['name'], entry['files']
        current = fingerprint(manager.target/name)
        prior = db['installed'].get(name)
        reason = None
        if prior is None and current is not None:
            reason = 'Unmanaged copy; adopt it before restoring.'
        elif prior and current != prior['files']:
            reason = 'Local edits or deletion since the receipt.'
        elif prior and current != desired and (prior.get('upstream_files') is None or current != prior['upstream_files']):
            reason = 'Preserved local customizations or an unbound source.'
        action = ('keep-local' if keep_local else 'blocked') if reason else (
            'unchanged' if current == desired else 'install' if current is None else 'update')
        incoming = {p: objects[sha] for p, sha in desired.items()}
        rows.append({'name': name, 'action': action, 'reason': reason, 'matches_lock': current == desired,
                     'managed': prior is not None, 'before_files': current, 'locked_files': desired,
                     'differences': manager.content_changes(manager.contents(manager.target/name), incoming)})
    return rows


def restore_preview(manager, path, keep_local=False):
    path = lock_path(manager, path)
    value, objects, sha = load(path)
    names = [e['name'] for e in value['skills']]
    before = stamp(manager, names)
    with staged(manager, value, objects):
        rows = restore_rows(manager, value, objects, keep_local)
    if before != stamp(manager, names) or sha != load(path)[2]:
        raise ValueError('Preview changed while reading; preview again')
    ok = all(row['action'] != 'blocked' for row in rows)
    plan_id = None
    if ok and any(row['action'] in ('install', 'update') for row in rows):
        plan_id = save_plan(manager, {'action': 'restore', 'path': str(path), 'lockfile_sha256': sha,
                                     'keep_local': keep_local, 'stamp': before})
    return {'ok': ok, 'action': 'restore-preview', 'target': str(manager.target), 'path': str(path),
            'sha256': sha, 'skills': rows, 'plan_id': plan_id,
            'matches_lock': all(row['matches_lock'] for row in rows),
            'note': 'No skills changed. Blocked entries stop the entire restore; keep-local skips them without altering receipts.'}


def check(manager, path):
    value, _, sha = load(lock_path(manager, path))
    rows = []
    db = manager.db()
    for entry in value['skills']:
        current = fingerprint(manager.target/entry['name'])
        matches = current == entry['files']
        rows.append({'name': entry['name'], 'matches_lock': matches, 'managed': entry['name'] in db['installed'],
                     'status': 'matching' if matches else 'missing' if current is None else 'different'})
    return {'ok': all(row['matches_lock'] for row in rows), 'target': str(manager.target),
            'sha256': sha, 'skills': rows, 'scope': 'Exact bytes of locked skills only; extra skills are untouched. No behavior or harness loading checks.'}


def write_atomic(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def restore_apply(manager, path, expected_sha, expected_stamp, keep_local):
    value, objects, sha = load(path)
    names = [e['name'] for e in value['skills']]
    if sha != expected_sha or stamp(manager, names) != expected_stamp:
        raise ValueError('Lock preview is stale; lockfile, skills or receipts changed')
    with staged(manager, value, objects) as root:
        rows = restore_rows(manager, value, objects, keep_local)
        if any(row['action'] == 'blocked' for row in rows):
            raise ValueError('Local modifications block this restore')
        changes = [row for row in rows if row['action'] in ('install', 'update')]
        original_db = read_bytes(manager.db_path) if manager.db_path.exists() else None
        original_names = list(manager.names)
        for row in changes:
            if row['before_files'] is not None:
                materialize(root/'before'/row['name'], row['before_files'],
                            {digest(data): data for data in manager.contents(manager.target/row['name']).values()})
        if stamp(manager, names) != expected_stamp or load(path)[2] != expected_sha:
            raise ValueError('Lock preview is stale; preview again')
        applied = []
        try:
            entries = {e['name']: e for e in value['skills']}
            for row in changes:
                name = row['name']
                if fingerprint(manager.target/name) != row['before_files']:
                    raise ValueError('Local files changed during restore')
                entry = entries[name]
                manager.record(name, root/'files'/name, root/'base'/name if entry['upstream_files'] is not None else None,
                               origin={'lockfile_sha256': sha, 'source_revision': entry['source_revision']})
                applied.append(row)
        except BaseException as error:
            try:
                for row in reversed(applied):
                    destination = manager.target/row['name']
                    if fingerprint(destination) != row['locked_files']:
                        raise ValueError('Concurrent edit prevents compensation: ' + row['name'])
                    replace_directory(destination, root/'before'/row['name'] if row['before_files'] is not None else None)
                if original_db is None:
                    if manager.db_path.exists():
                        manager.db_path.unlink()
                else:
                    write_atomic(manager.db_path, original_db)
                manager.names = original_names
            except BaseException as recovery:
                raise ValueError('Restore failed and compensation was incomplete; inspect target and receipts: ' + str(recovery)) from error
            raise
    matches = all(fingerprint(manager.target/e['name']) == e['files'] for e in value['skills'])
    return {'ok': True, 'action': 'restored', 'target': str(manager.target), 'sha256': sha,
            'matches_lock': matches, 'kept_local': [row['name'] for row in rows if row['action'] == 'keep-local'],
            'changed': [row['name'] for row in changes], 'scope': 'File integrity only; no skills or agents were executed.'}


def apply(manager, plan_id):
    if not isinstance(plan_id, str) or not re.fullmatch('[a-f0-9]{32}', plan_id):
        raise ValueError('Invalid lock plan ID')
    folder = safe_path(manager.state/'lock-plans')
    path = folder/(plan_id+'.json')
    record = parse_json(read_bytes(path, MAX_JSON + 65536).decode('utf-8'))
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int or record.get('schema_version') != 1
            or record.get('binding') != binding(manager) or record.get('action') not in ('create', 'restore')):
        raise ValueError('Lock plan does not match this configuration')
    expiry = record.get('expires_at')
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or not time.time() < expiry <= time.time()+601:
        raise ValueError('Lock preview expired; preview again')
    output = lock_path(manager, record['path'])
    # Both claim and execution are protected by the same existing operation lock.
    with manager.lock():
        claimed = path.with_suffix('.claimed')
        if claimed.exists():
            raise ValueError('Lock plan already claimed')
        path.rename(claimed)
        try:
            if record['action'] == 'restore':
                if type(record.get('keep_local')) is not bool:
                    raise ValueError('Invalid restore strategy')
                return restore_apply(manager, output, record['lockfile_sha256'], record['stamp'], record['keep_local'])
            value = record['value']
            validate(value)
            current, before = capture(manager, [e['name'] for e in value['skills']])
            if before != record['stamp'] or current != value:
                raise ValueError('Lock preview is stale; installed files or receipts changed')
            previous = load(output)[2] if output.exists() else None
            if previous != record['previous_sha256']:
                raise ValueError('Lockfile changed after preview')
            output.parent.mkdir(parents=True, exist_ok=True)
            data = encode(value)
            if previous is None:
                with output.open('xb') as stream:
                    stream.write(data)
            else:
                write_atomic(output, data)
            return {'ok': True, 'action': 'lockfile-saved', 'path': str(output), 'sha256': digest(data), 'skills': summary(value)}
        finally:
            claimed.unlink()
