"""Local learning receipts, factual impact summaries and bounded text comparisons."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import uuid

from skillctl import fingerprint, write_json
from skillctl_content import is_link, valid_name
from skill_quality import prepare, grade, digest, request_digest, parse_json, _check_plan

LIMIT = 2 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_object(path):
    with Path(path).open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('Artifact exceeds 2 MiB')
    value = parse_json(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def unlinked(path):
    path = Path(path).absolute()
    if any(is_link(p) for p in [path, *path.parents]):
        raise ValueError('Linked artifact paths are not supported')
    return path


def fresh_directory(path):
    path = unlinked(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(field + ' must contain 1..4000 characters')
    return value


def record_learning(manager, value):
    """Record evidence metadata, never execute it or promote its claims to verified."""
    required = {'schema_version', 'skill', 'scope', 'trigger', 'lesson', 'decision', 'reason', 'evidence'}
    if set(value) != required or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Learning input requires exactly: ' + ', '.join(sorted(required)))
    name = value['skill']
    if not isinstance(name, str) or not valid_name(name):
        raise ValueError('Invalid skill name')
    if value['decision'] not in ('create', 'update', 'skip'):
        raise ValueError('decision must be create, update or skip')
    for field in ('trigger', 'lesson', 'reason'):
        text(value[field], field)
    scope = value['scope']
    if not isinstance(scope, dict) or scope.get('kind') not in ('project', 'global'):
        raise ValueError('Explicit project/global scope is required')
    if scope['kind'] == 'project':
        if set(scope) != {'kind', 'path'} or not isinstance(scope['path'], str):
            raise ValueError('Project scope needs a path')
        project = Path(scope['path']).expanduser()
        if not project.is_absolute() or not project.is_dir():
            raise ValueError('Project scope must identify an existing absolute directory')
        scope = {'kind': 'project', 'path': str(project.resolve())}
    elif set(scope) != {'kind'}:
        raise ValueError('Global scope accepts only kind')
    evidence = value['evidence']
    if not isinstance(evidence, list) or len(evidence) > 20 or (not evidence and value['decision'] != 'skip'):
        raise ValueError('Provide 1..20 evidence files, or an empty list for skip')
    entries = []
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) != {'path', 'summary'}:
            raise ValueError('Evidence needs path and summary')
        path = Path(text(entry['path'], 'evidence.path')).expanduser()
        if not path.is_absolute():
            raise ValueError('Evidence path must be absolute')
        path = unlinked(path)
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''):
                sha.update(block)
        entries.append({'path': str(path), 'sha256': sha.hexdigest(),
                        'summary': text(entry['summary'], 'evidence.summary')})
    prior = fingerprint(manager.target/name)
    upstream = manager.validate(name) if name in manager.sources else None
    if value['decision'] == 'update' and prior is None and upstream is None:
        raise ValueError('Update candidate needs an existing installed or source skill')
    if value['decision'] == 'create' and (prior is not None or upstream is not None):
        raise ValueError('Skill already exists; choose update or skip')
    receipt = {**value, 'id': uuid.uuid4().hex, 'created_at': now(), 'scope': scope,
               'evidence': entries, 'status': 'skipped' if value['decision'] == 'skip' else 'observed',
               'target': str(manager.target), 'installed_files': prior, 'source_files': upstream,
               'verification': 'Evidence bytes recorded; lesson and claimed results are not independently verified.',
               'installed': False}
    receipt['receipt_sha256'] = digest(receipt)
    folder = unlinked(manager.state/'learning')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(receipt['id']+'.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return {'ok': True, 'receipt': receipt, 'path': str(path)}


def learning_receipts(manager, receipt_id=None):
    folder = unlinked(manager.state/'learning')
    if receipt_id is not None and not re.fullmatch('[a-f0-9]{32}', receipt_id):
        raise ValueError('Invalid receipt ID')
    paths = [folder/(receipt_id+'.json')] if receipt_id else sorted(folder.glob('*.json'))
    result = []
    for path in paths:
        receipt = read_object(unlinked(path))
        sha = receipt.pop('receipt_sha256', None)
        if digest(receipt) != sha or receipt.get('id') != path.stem or receipt.get('target') != str(manager.target):
            raise ValueError('Learning receipt integrity/target mismatch')
        changed = []
        for entry in receipt['evidence']:
            try:
                with unlinked(entry['path']).open('rb') as stream:
                    current = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
                    if current is None:
                        h = hashlib.sha256()
                        for block in iter(lambda: stream.read(65536), b''): h.update(block)
                        current = h.hexdigest()
                state = 'matched' if current == entry['sha256'] else 'changed'
            except (ValueError, OSError):
                state = 'unavailable'
            changed.append({'path': entry['path'], 'status': state})
        result.append({**receipt, 'receipt_sha256': sha, 'evidence_now': changed,
                       'installed_baseline_current': fingerprint(manager.target/receipt['skill']) == receipt['installed_files']})
    return {'ok': True, 'receipts': result}


def impact(manager, name):
    """Describe observable edits; never infer semantic safety from a text diff."""
    if not isinstance(name, str) or not valid_name(name):
        raise ValueError('Invalid skill name')
    stamp = manager.preview_stamp(name)
    comparison = manager.diff(name, True)
    rows = []
    changed = set(comparison['added']+comparison['changed']+comparison['removed'])
    for entry in comparison['files']:
        if entry['file'] not in changed:
            continue
        patch = entry['local_vs_upstream']
        lines = (patch.get('diff') or '').splitlines()
        # Start after the two unified-diff file headers; retain original +/- evidence.
        added = [line[1:] for line in lines[2:] if line.startswith('+')]
        removed = [line[1:] for line in lines[2:] if line.startswith('-')]
        path = entry['file']
        category = ('instructions' if path == 'SKILL.md' else 'script' if path.startswith('scripts/')
                    else 'reference' if path.startswith('references/') else 'other')
        advice = {'instructions': '重測觸發與行為情境', 'script': '重測腳本成功、失敗與重跑',
                  'reference': '檢查引用及受影響的工作情境', 'other': '確認資源與依賴的相容性'}[category]
        rows.append({'file': path, 'category': category, 'change': 'removed' if path in comparison['removed'] else
                     'added' if path in comparison['added'] else 'changed',
                     'added_line_count': len(added), 'removed_line_count': len(removed),
                     'added_excerpt': added[:12], 'removed_excerpt': removed[:12],
                     'excerpt_truncated': len(added)>12 or len(removed)>12,
                     'text_available': patch['kind']=='text', 'recheck': advice,
                     'review_removed_guidance': bool(removed) or path in comparison['removed']})
    manager.verify_preview(name, stamp)
    return {'ok': True, 'name': name, 'target': str(manager.target), 'status': comparison['status'],
            'blocked_reason': comparison['blocked_reason'], 'baseline_available': comparison['baseline_available'],
            'files': rows, 'comparison': comparison, 'preview_stamp': stamp,
            'summary': f'新增 {len(comparison["added"])}、修改 {len(comparison["changed"])}、刪除 {len(comparison["removed"])} 個檔案。',
            'semantic_assessment': '需人工／agent 核對；新增文字不等於新增能力，移除文字可能包含重要指引。',
            'source_scope': 'Configured local source only; not a remote latest-version check.', 'applied': False}


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be {low}..{high}')
    return value


def prepare_comparison(suite, old, new, model, max_calls=8, max_tokens=1024, timeout=60, total_timeout=300):
    limits = {'max_calls': integer(max_calls, 2, 40, 'max_calls'),
              'max_tokens': integer(max_tokens, 64, 4096, 'max_tokens'),
              'timeout': integer(timeout, 1, 180, 'timeout'),
              'total_timeout': integer(total_timeout, 1, 3600, 'total_timeout')}
    names = {case['skill'] for case in suite.get('cases', [])}
    if len(names) != 1:
        raise ValueError('Compare exactly one skill per plan')
    name = next(iter(names))
    if not isinstance(name, str) or not valid_name(name):
        raise ValueError('Invalid skill name')
    if 2*len(suite['cases']) > max_calls:
        raise ValueError('Comparison exceeds max_calls; reduce cases or explicitly raise the limit')
    versions = {side: prepare(suite, {name: Path(folder)}, model) for side, folder in [('old', old), ('new', new)]}
    for plan in versions.values():
        for job in plan['jobs']:
            job.update(max_tokens=max_tokens, timeout=timeout)
            if len(json.dumps(job, ensure_ascii=False).encode('utf-8')) > 128 * 1024:
                raise ValueError('Each comparison request must fit within 128 KiB')
    result = {'schema_version': 1, 'mode': 'paired-text-simulation', 'created_at': now(),
              'skill': name, 'limits': limits, 'versions': versions,
              'paths': {'old': str(Path(old).resolve()), 'new': str(Path(new).resolve())},
              'planned_calls': 2*len(suite['cases']), 'max_requested_output_tokens': 2*len(suite['cases'])*max_tokens,
              'note': 'SKILL.md text only; no tool loading, references, device actions or automatic retries. Adapter must honor limits. Provider billing is not capped.'}
    result['plan_sha256'] = digest(result)
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > LIMIT:
        raise ValueError('Comparison plan exceeds 2 MiB; reduce cases or skill text')
    return result


def check_comparison(plan):
    unsigned = {k:v for k,v in plan.items() if k != 'plan_sha256'}
    if plan.get('plan_sha256') != digest(unsigned) or plan.get('mode') != 'paired-text-simulation':
        raise ValueError('Comparison plan integrity mismatch')
    versions = plan['versions']
    if set(versions) != {'old', 'new'}:
        raise ValueError('Expected old and new versions')
    for version in versions.values(): _check_plan(version)
    if versions['old']['suite'] != versions['new']['suite'] or versions['old']['model'] != versions['new']['model']:
        raise ValueError('Both versions must use the same suite and model')
    limits = plan['limits']
    integer(limits['max_calls'], 2, 40, 'max_calls')
    integer(limits['max_tokens'], 64, 4096, 'max_tokens')
    integer(limits['timeout'], 1, 180, 'timeout')
    integer(limits['total_timeout'], 1, 3600, 'total_timeout')
    count = sum(len(v['jobs']) for v in versions.values())
    if count != plan['planned_calls'] or count > limits['max_calls'] or plan['max_requested_output_tokens'] != count*limits['max_tokens']:
        raise ValueError('Invalid comparison budget')
    for side, version in versions.items():
        if list(version['skills']) != [plan['skill']]:
            raise ValueError('Comparison skill mismatch')
        if fingerprint(Path(plan['paths'][side])) != version['skills'][plan['skill']]['files']:
            raise ValueError('Skill files changed since preparation; prepare again')
        canonical = prepare(version['suite'], {plan['skill']: Path(plan['paths'][side])}, version['model'])
        for job in canonical['jobs']:
            job.update(max_tokens=limits['max_tokens'], timeout=limits['timeout'])
        if version != canonical:
            raise ValueError('Comparison requests differ from the declared suite and skill files')
        for job in version['jobs']:
            if job.get('max_tokens') != limits['max_tokens'] or job.get('timeout') != limits['timeout']:
                raise ValueError('Job limits differ from the plan')
            if len(json.dumps(job, ensure_ascii=False).encode('utf-8')) > 128 * 1024:
                raise ValueError('Each comparison request must fit within 128 KiB')


def stop_process(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    if process.poll() is None: process.kill()
    process.wait(timeout=10)


def call_adapter(argv, request, timeout):
    """Explicit trusted adapter, one request on stdin, one receipt on stdout."""
    with tempfile.TemporaryDirectory(prefix='skillctl-adapter-') as tmp:
        folder = Path(tmp)
        incoming = folder/'input.json'; incoming.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        stdout, stderr = folder/'stdout', folder/'stderr'
        with incoming.open('rb') as inp, stdout.open('wb') as out, stderr.open('wb') as err:
            process = subprocess.Popen(argv, stdin=inp, stdout=out, stderr=err, cwd=folder,
                                       start_new_session=os.name != 'nt')
            start = time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic()-start >= timeout:
                        raise TimeoutError('Adapter timeout; remote generation/billing may continue')
                    if stdout.stat().st_size > LIMIT or stderr.stat().st_size > LIMIT:
                        raise ValueError('Adapter output exceeds size limit')
                    time.sleep(0.02)
            except BaseException:
                stop_process(process)
                raise
        if process.returncode != 0:
            raise ValueError('Adapter failed with exit code '+str(process.returncode)+'; stderr not copied into report')
        return read_object(stdout)


def run_comparison(plan, adapter, output):
    check_comparison(plan)
    if set(adapter) != {'argv', 'kind'} or adapter['kind'] not in ('model', 'fixture'):
        raise ValueError('Adapter requires argv and kind=model|fixture')
    argv = adapter['argv']
    if not isinstance(argv, list) or not argv or not all(isinstance(s, str) and s for s in argv) or not Path(argv[0]).is_absolute():
        raise ValueError('Adapter argv needs an absolute executable path and string arguments')
    # An exclusive run directory is also the durable claim: never reuse/retry a run.
    out = fresh_directory(output)
    write_json(out/'claim.json', {'plan_sha256': plan['plan_sha256'], 'adapter_sha256': digest(adapter), 'started_at': now()})
    started = time.monotonic()
    responses = {'old': [], 'new': []}
    attempts, halted = [], None
    limits = plan['limits']
    # Alternate the first side per case, keeping matched prompts adjacent.
    jobs = plan['versions']['old']['jobs']
    for index in range(len(jobs)):
        for side in (('old','new') if index % 2 == 0 else ('new','old')):
            remaining = limits['total_timeout']-(time.monotonic()-started)
            if remaining <= 0:
                halted = 'Total time budget exhausted'; break
            if len(attempts) >= limits['max_calls']:
                halted = 'Call budget exhausted'; break
            job = plan['versions'][side]['jobs'][index]
            request = {**job, 'request_sha256': request_digest(job)}
            attempt = {'side': side, 'id': job['id'], 'status': 'started'}
            attempts.append(attempt)
            write_json(out/'progress.json', {'attempts': attempts, 'plan_sha256': plan['plan_sha256']})
            try:
                response = call_adapter(argv, request, min(limits['timeout'], remaining))
                if any(response.get(k) != request[k] for k in ('id', 'model', 'request_sha256')):
                    raise ValueError('Adapter response does not match the request')
                responses[side].append(response)
                write_json(out/f'{side}-{index}.json', response)
                partial = grade(plan['versions'][side], responses[side])
                case = partial['skills'][plan['skill']]['cases'][index]
                if case['status'] == 'error':
                    raise ValueError(case['error'])
                attempt['status'] = 'completed'
            except (ValueError, OSError, TimeoutError, subprocess.SubprocessError) as error:
                attempt.update(status='error', error=str(error)); halted = str(error)
                break
        if halted: break
    reports = {side: grade(plan['versions'][side], responses[side]) for side in ('old', 'new')}
    pairs = []
    for a,b in zip(reports['old']['skills'][plan['skill']]['cases'], reports['new']['skills'][plan['skill']]['cases']):
        verdict = ('inconclusive' if 'error' in (a['status'],b['status']) else
                   'improved' if a['status']=='failed' and b['status']=='passed' else
                   'regressed' if a['status']=='passed' and b['status']=='failed' else 'unchanged')
        pairs.append({'id': a['id'], 'old': a['status'], 'new': b['status'], 'verdict': verdict})
    known_tokens = 0; usage_complete = True
    for entries in responses.values():
        for entry in entries:
            total = entry.get('usage', {}).get('total_tokens') if isinstance(entry.get('usage'), dict) else None
            if type(total) is int and total >= 0: known_tokens += total
            else: usage_complete = False
    usage_complete = usage_complete and len(attempts) == sum(map(len, responses.values()))
    complete = not halted and len(attempts) == plan['planned_calls']
    # Overall execution success and candidate quality are separate decisions.
    # A higher aggregate score must never hide an individual regression.
    assessment = ('inconclusive' if not complete or any(p['verdict']=='inconclusive' for p in pairs) else
                  'regressed' if any(p['verdict']=='regressed' for p in pairs) else
                  'failed_checks' if any(p['new']=='failed' for p in pairs) else 'passed_checks')
    result = {'ok': complete, 'status': 'completed' if complete else 'incomplete', 'mode': plan['mode'],
              'candidate_assessment': assessment,
              'adapter_kind': adapter['kind'], 'is_fixture': adapter['kind']=='fixture',
              'created_at': now(), 'plan_sha256': plan['plan_sha256'], 'model': plan['versions']['old']['model'],
              'limits': limits, 'attempted_calls': len(attempts), 'planned_calls': plan['planned_calls'],
              'halted_reason': halted, 'pairs': pairs, 'reports': reports,
              'usage': {'known_tokens': known_tokens, 'total_tokens': known_tokens if usage_complete and attempts else None,
                        'cost': None, 'note': 'Adapter-reported usage; not a provider billing statement.'},
              'note': 'Text-only simulation of supplied SKILL.md. No harness loading or device behavior was verified. A completed comparison is not a passing candidate.'}
    write_json(out/'progress.json', {'attempts': attempts, 'plan_sha256': plan['plan_sha256']})
    write_json(out/'report.json', result)
    return result
