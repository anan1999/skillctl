"""Synthetic text-simulation quality evaluation for skills; Python standard library only.

Plans contain SKILL.md content and are private local artifacts. Reports contain
only hashes, relative file names and the model's JSON answers.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from skillctl import fingerprint, write_json
from skillctl_content import valid_name

MODE = 'text-simulation'
MAX_TOKENS, TIMEOUT, BATCH_SIZE = 1800, 120, 4
SAFE_ID = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
FIELD = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
KIND = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
CASE_KEYS = {'id', 'skill', 'kind', 'task', 'input', 'response_schema', 'checks'}
CASE_STATUS = ('passed', 'failed', 'error')
FINISHED = (None, 'stop', 'end_turn', 'completed', 'stop_sequence')
TEXT_KEYS = ('response', 'text', 'output', 'content', 'result', 'message', 'answer', 'output_text', 'response_text')
INSTRUCTIONS = (
    'This is a text-only simulation: no tools, commands or actions are available or executed, '
    'and you must not claim to have run anything. Use only the SKILL.md instructions in the context '
    'and answer the case from its input. Reply with exactly one JSON object and nothing else '
    '(no markdown, no code fences) whose keys are exactly the response_schema keys.')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key[:80])
        result[key] = value
    return result


def _constant(token):
    raise ValueError('Non-finite JSON number: ' + token)


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except RecursionError:
        raise ValueError('JSON is nested too deeply')


def load_json(path):
    return parse_json(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('Value is not canonical JSON')
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def request_digest(job):
    """Bind an imported answer to its exact task, context, model and case ID."""
    return digest({key: job[key] for key in ('id', 'model', 'task', 'context')})


def bind_responses(plan, responses):
    """Validate adapter input provenance before importing receipts through the CLI."""
    _check_plan(plan)
    jobs = {job['id']: job for job in plan['jobs']}
    bound = []
    for item in responses:
        if not isinstance(item, dict) or item.get('id') not in jobs:
            raise ValueError('Receipt contains an unknown job')
        job = jobs[item['id']]
        expected = request_digest(job)
        has_inputs = 'task' in item or 'context' in item
        if has_inputs and any(item.get(key) != job[key] for key in ('task', 'context', 'model')):
            raise ValueError('Receipt inputs do not match plan: ' + job['id'])
        supplied = item.get('request_sha256')
        if supplied is not None and supplied != expected:
            raise ValueError('Receipt request hash does not match plan: ' + job['id'])
        if not has_inputs and supplied != expected:
            raise ValueError('Receipt needs original task/context or request_sha256: ' + job['id'])
        bound.append({**item, 'request_sha256': expected})
    return bound


def _clip(text, limit=160):
    return text if len(text) <= limit else text[:limit] + '...'


def _string(value, what):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(what + ' must be a nonempty string')
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def strict_equal(actual, expected):
    """JSON equality where booleans are never numbers."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected
    if _is_number(actual) and _is_number(expected):
        return actual == expected
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(strict_equal(a, e) for a, e in zip(actual, expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected
    return actual is None and expected is None


def check_suite(suite):
    """Validate a suite and return its cases; unknown or duplicate IDs fail closed."""
    if not isinstance(suite, dict) or type(suite.get('schema_version')) is not int or suite['schema_version'] != 1:
        raise ValueError('Suite must be an object with schema_version 1')
    if not isinstance(suite.get('id'), str) or not SAFE_ID.match(suite['id']):
        raise ValueError('Suite id is missing or unsafe')
    cases = suite.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('Suite must contain at least one case')
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            raise ValueError('Each case must have exactly: ' + ', '.join(sorted(CASE_KEYS)))
        if not isinstance(case['id'], str) or not SAFE_ID.match(case['id']):
            raise ValueError('Unsafe case id')
        if case['id'] in seen:
            raise ValueError('Duplicate case id: ' + case['id'])
        seen.add(case['id'])
        if not isinstance(case['skill'], str) or not valid_name(case['skill']):
            raise ValueError('Unsafe skill name in case ' + case['id'])
        if not isinstance(case['kind'], str) or not KIND.match(case['kind']):
            raise ValueError('Unsafe kind in case ' + case['id'])
        _string(case['task'], 'task of ' + case['id'])
        _string(case['input'], 'input of ' + case['id'])
        schema = case['response_schema']
        if not isinstance(schema, dict) or not schema:
            raise ValueError('response_schema of ' + case['id'] + ' must be a nonempty object')
        for key, text in schema.items():
            if not FIELD.match(key):
                raise ValueError('Unsafe response field in case ' + case['id'])
            _string(text, 'response_schema description')
        checks = case['checks']
        if not isinstance(checks, list) or not checks:
            raise ValueError('Case ' + case['id'] + ' needs at least one check')
        paths = set()
        for check in checks:
            if not isinstance(check, dict) or set(check) != {'path', 'op', 'value'}:
                raise ValueError('Each check must have exactly path, op and value')
            if check['op'] != 'eq':
                raise ValueError('Unsupported check operation in case ' + case['id'])
            if check['path'] not in schema or check['path'] in paths:
                raise ValueError('Unknown or duplicate check path in case ' + case['id'])
            paths.add(check['path'])
            digest(check['value'])
    return cases


def _load_skill(name, folder):
    try:
        files = fingerprint(Path(folder))
    except (ValueError, OSError):
        raise ValueError('Skill folder is linked or unreadable: ' + name)
    if not files or 'SKILL.md' not in files:
        raise ValueError('Skill folder is missing or has no SKILL.md: ' + name)
    data = (Path(folder) / 'SKILL.md').read_bytes()
    if hashlib.sha256(data).hexdigest() != files['SKILL.md']:
        raise ValueError('SKILL.md changed while preparing: ' + name)
    text = data.decode('utf-8-sig')
    body = re.sub(r'\A---\s*\n.*?\n---(?:\s*\n|$)', '', text, count=1, flags=re.S)
    if not body.strip():
        raise ValueError('SKILL.md has no instructions: ' + name)
    return files, text


def prepare(suite, skill_dirs, model):
    cases = check_suite(suite)
    if not isinstance(model, str) or not MODEL.match(model):
        raise ValueError('Unsafe or empty model name')
    skills, texts = {}, {}
    for name in dict.fromkeys(case['skill'] for case in cases):
        if name not in skill_dirs:
            raise ValueError('No skill folder supplied for: ' + name)
        files, texts[name] = _load_skill(name, skill_dirs[name])
        skills[name] = {'files': files}
    jobs = []
    for case in cases:
        context = '\n\n'.join([
            '## SKILL.md\n' + texts[case['skill']], '## Case task\n' + case['task'],
            '## Input\n' + case['input'],
            '## response_schema\n' + json.dumps(case['response_schema'], ensure_ascii=False, indent=2)])
        jobs.append({'id': case['id'], 'model': model, 'task': INSTRUCTIONS, 'context': context,
                     'max_tokens': MAX_TOKENS, 'timeout': TIMEOUT})
    return {'schema_version': 1, 'mode': MODE, 'suite': copy.deepcopy(suite), 'suite_sha256': digest(suite),
            'model': model, 'skills': skills, 'jobs': jobs}


def _valid_files(files):
    if not isinstance(files, dict) or 'SKILL.md' not in files:
        return False
    for path, value in files.items():
        if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or ':' in path \
                or '..' in path.split('/') or not isinstance(value, str) or not SHA.match(value):
            return False
    return True


def _check_plan(plan):
    if not isinstance(plan, dict) or plan.get('mode') != MODE or plan.get('schema_version') != 1:
        raise ValueError('Not a quality plan')
    cases = check_suite(plan.get('suite'))
    if plan.get('suite_sha256') != digest(plan['suite']):
        raise ValueError('Plan suite hash does not match its suite')
    model = plan.get('model')
    if not isinstance(model, str) or not MODEL.match(model):
        raise ValueError('Plan model is invalid')
    names = list(dict.fromkeys(case['skill'] for case in cases))
    skills = plan.get('skills')
    if not isinstance(skills, dict) or list(skills) != names or not all(
            isinstance(v, dict) and _valid_files(v.get('files')) for v in skills.values()):
        raise ValueError('Plan skills do not match its suite')
    jobs = plan.get('jobs')
    if not isinstance(jobs, list) or [j.get('id') if isinstance(j, dict) else None for j in jobs] != [
            case['id'] for case in cases] or any(job.get('model') != model for job in jobs):
        raise ValueError('Plan jobs do not match its suite and model')
    return cases, model


def _number(value):
    return value if _is_number(value) and value >= 0 else None


def _answer(item):
    for key in TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, list) and value and all(isinstance(b, dict) and isinstance(b.get('text'), str) for b in value):
            return ''.join(b['text'] for b in value)
        if isinstance(value, dict) and any(k in value for k in TEXT_KEYS):
            return _answer(value)
        if value is not None:
            return value
    return None


def _evaluate(case, model, items):
    """Return (answer, error); only a complete, exact JSON answer is returned."""
    if not items:
        return None, 'Missing response'
    if len(items) > 1:
        return None, 'Duplicate response'
    item = items[0]
    if item.get('model') != model:
        return None, 'Response model does not match the plan'
    if item.get('status') != 'completed':
        return None, 'Response did not complete'
    if item.get('truncated') is True or item.get('finish_reason') not in FINISHED \
            or item.get('stop_reason') not in FINISHED:
        return None, 'Response was truncated or stopped early'
    raw = _answer(item)
    if isinstance(raw, str):
        try:
            raw = parse_json(raw.strip())
        except ValueError as error:
            return None, 'Response is not a valid JSON object: ' + _clip(str(error))
    if not isinstance(raw, dict):
        return None, 'Response is not a JSON object'
    expected = set(case['response_schema'])
    if set(raw) != expected:
        missing, extra = sorted(expected - set(raw)), sorted(set(raw) - expected)
        return None, _clip('Response fields differ from schema; missing: %s; unexpected: %s' % (
            ', '.join(missing) or '-', ', '.join(extra) or '-'))
    return raw, None


def grade(plan, responses):
    cases, model = _check_plan(plan)
    if not isinstance(responses, list) or not all(isinstance(item, dict) for item in responses):
        raise ValueError('Responses must be a list of objects')
    by_id = {}
    known = {case['id'] for case in cases}
    for item in responses:
        job_id = item.get('id', item.get('job_id'))
        if not isinstance(job_id, str) or job_id not in known:
            raise ValueError('Response for an unknown job')
        by_id.setdefault(job_id, []).append(item)
    skills = {name: {'files': copy.deepcopy(entry['files']), 'cases': []} for name, entry in plan['skills'].items()}
    request_hashes = {job['id']: request_digest(job) for job in plan['jobs']}
    all_bound = True
    for case in cases:
        items = by_id.get(case['id'], [])
        answer, error = _evaluate(case, model, items)
        supplied = items[0].get('request_sha256') if len(items) == 1 else None
        bound = supplied == request_hashes[case['id']]
        all_bound = all_bound and bound
        if supplied is not None and not bound:
            answer, error = None, 'Response request hash does not match the plan'
        checks = []
        for check in case['checks']:
            actual = answer[check['path']] if answer is not None else None
            checks.append({'path': check['path'], 'expected': copy.deepcopy(check['value']), 'actual': actual,
                           'passed': answer is not None and strict_equal(actual, check['value'])})
        entry = {'id': case['id'], 'kind': case['kind'], 'checks': checks,
                 'status': 'error' if error else 'passed' if all(c['passed'] for c in checks) else 'failed'}
        if bound:
            entry['request_sha256'] = supplied
        if error:
            entry['error'] = error
        if len(items) == 1:
            if _number(items[0].get('elapsed_seconds')) is not None:
                entry['elapsed_seconds'] = items[0]['elapsed_seconds']
            usage = items[0].get('usage')
            if isinstance(usage, dict) and all(isinstance(k, str) and _number(v) is not None for k, v in usage.items()):
                entry['usage'] = dict(usage)
        skills[case['skill']]['cases'].append(entry)
    return {'schema_version': 1, 'mode': MODE, 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'model': model, 'suite_sha256': plan['suite_sha256'],
            'request_binding': 'matched' if all_bound else 'not-recorded', 'skills': skills}


def _check_report(report):
    if not isinstance(report, dict) or type(report.get('schema_version')) is not int or report['schema_version'] != 1:
        raise ValueError('Unsupported report schema')
    if report.get('mode') != MODE:
        raise ValueError('Report mode is not text-simulation')
    if report.get('request_binding', 'not-recorded') not in ('matched', 'not-recorded'):
        raise ValueError('Report request binding is invalid')
    if not isinstance(report.get('model'), str) or not MODEL.match(report['model']):
        raise ValueError('Report model is invalid')
    stamp = report.get('created_at')
    try:
        if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError
    except ValueError:
        raise ValueError('Report created_at is not a timezone-aware ISO time')
    if not isinstance(report.get('suite_sha256'), str) or not SHA.match(report['suite_sha256']):
        raise ValueError('Report suite hash is invalid')
    skills = report.get('skills')
    if not isinstance(skills, dict) or not skills:
        raise ValueError('Report has no skills')
    ids = set()
    for name, entry in skills.items():
        if not valid_name(name) or not isinstance(entry, dict) or not _valid_files(entry.get('files')):
            raise ValueError('Report skill entry is invalid: ' + _clip(str(name), 60))
        if not isinstance(entry.get('cases'), list) or not entry['cases']:
            raise ValueError('Report skill has no cases: ' + name)
        for case in entry['cases']:
            if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not SAFE_ID.match(case['id']) \
                    or case['id'] in ids or case.get('status') not in CASE_STATUS \
                    or not isinstance(case.get('kind'), str) or not KIND.match(case['kind']):
                raise ValueError('Report case is invalid in skill ' + name)
            ids.add(case['id'])
            if report.get('request_binding') == 'matched' and (
                    not isinstance(case.get('request_sha256'), str) or not SHA.fullmatch(case['request_sha256'])):
                raise ValueError('Report lacks its claimed request binding')
            checks = case.get('checks')
            if not isinstance(checks, list) or not checks:
                raise ValueError('Report case has no checks: ' + case['id'])
            results = []
            for check in checks:
                if not isinstance(check, dict) or set(check) != {'path', 'expected', 'actual', 'passed'} \
                        or not isinstance(check['path'], str) or not FIELD.match(check['path']) \
                        or type(check['passed']) is not bool:
                    raise ValueError('Report check is invalid: ' + case['id'])
                results.append(strict_equal(check['actual'], check['expected']))
            if case['status'] == 'error':
                if not isinstance(case.get('error'), str) or any(c['passed'] for c in checks):
                    raise ValueError('Report error case is inconsistent: ' + case['id'])
                continue
            if [c['passed'] for c in checks] != results or case['status'] != ('passed' if all(results) else 'failed'):
                raise ValueError('Report claims do not match recomputed checks: ' + case['id'])


def assess(report, name, files):
    if not valid_name(name):
        raise ValueError('Invalid skill name')
    result = {'name': name, 'status': 'untested', 'passed': 0, 'total': 0, 'cases': [], 'model': None,
              'created_at': None, 'mode': None, 'files': None, 'version': None, 'suite_sha256': None}
    if report is None:
        return result
    try:
        _check_report(report)
    except ValueError as error:
        return {**result, 'status': 'error', 'error': str(error)}
    entry = report['skills'].get(name)
    if entry is None:
        return result
    cases = copy.deepcopy(entry['cases'])
    passed = sum(case['status'] == 'passed' for case in cases)
    status = 'stale' if files != entry['files'] else 'passed' if passed == len(cases) else 'failed'
    return {**result, 'status': status, 'passed': passed, 'total': len(cases), 'cases': cases,
            'model': report['model'], 'created_at': report['created_at'], 'mode': report['mode'],
            'files': copy.deepcopy(entry['files']), 'version': digest(entry['files']),
            'suite_sha256': report['suite_sha256'], 'request_binding': report.get('request_binding', 'not-recorded')}


def _write_prepare(args):
    suite = load_json(args.suite)
    root = Path(args.skill_root)
    plan = prepare(suite, {case['skill']: root / case['skill'] for case in suite['cases']}, args.model)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('gateway-batch-*.json'):
        old.unlink()
    write_json(out / 'plan.json', plan)
    jobs = plan['jobs']
    batches = [jobs[i:i + BATCH_SIZE] for i in range(0, len(jobs), BATCH_SIZE)]
    for number, batch in enumerate(batches, 1):
        write_json(out / ('gateway-batch-%d.json' % number), {'jobs': batch})
    print(json.dumps({'plan': 'plan.json', 'jobs': len(jobs), 'batches': len(batches)}))
    return 0


def _write_grade(args):
    plan = load_json(args.plan)
    responses = []
    for path in args.receipt:
        receipt = load_json(path)
        if not isinstance(receipt, dict) or not isinstance(receipt.get('jobs'), list):
            raise ValueError('Receipt must be an object with a jobs list')
        responses.extend(receipt['jobs'])
    report = grade(plan, bind_responses(plan, responses))
    write_json(Path(args.output), report)
    cases = [case for entry in report['skills'].values() for case in entry['cases']]
    passed = sum(case['status'] == 'passed' for case in cases)
    print(json.dumps({'passed': passed, 'total': len(cases)}))
    return 0 if passed == len(cases) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='Write a private plan and Gateway job batches; never calls the network')
    prep.add_argument('--suite', type=Path, required=True)
    prep.add_argument('--skill-root', type=Path, required=True)
    prep.add_argument('--model', required=True)
    prep.add_argument('--output', type=Path, required=True)
    gr = sub.add_parser('grade', help='Grade Gateway receipts against a plan')
    gr.add_argument('--plan', type=Path, required=True)
    gr.add_argument('--receipt', type=Path, action='append', required=True)
    gr.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return _write_prepare(args) if args.command == 'prepare' else _write_grade(args)
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
