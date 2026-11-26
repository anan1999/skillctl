import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_quality as q
from skillctl import fingerprint

SECRET_ANSWER = 'SENTINEL-EXPECTED-ANSWER'
BODY = 'Use the evidence only.\nNever invent a cause.\n'


def make_case(number, **changes):
    case = {'id': 'case-%d' % number, 'skill': 'demo-skill', 'kind': 'evidence', 'task': 'Diagnose only.',
            'input': 'line %d' % number,
            'response_schema': {'line': 'integer', 'verified': 'boolean', 'label': 'string'},
            'checks': [{'path': 'line', 'op': 'eq', 'value': 3}, {'path': 'verified', 'op': 'eq', 'value': False},
                       {'path': 'label', 'op': 'eq', 'value': SECRET_ANSWER}]}
    case.update(changes)
    return case


def make_suite(count=5):
    return {'schema_version': 1, 'id': 'demo-suite', 'description': 'demo', 'cases': [make_case(i) for i in range(count)]}


GOOD = {'line': 3, 'verified': False, 'label': SECRET_ANSWER}


def reply(job_id, answer=GOOD, **extra):
    item = {'id': job_id, 'model': 'test-model', 'status': 'completed', 'finish_reason': 'stop',
            'response': answer if isinstance(answer, str) else json.dumps(answer)}
    item.update(extra)
    return item


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'skills'
        folder = self.root / 'demo-skill'
        (folder / 'refs').mkdir(parents=True)
        (folder / 'SKILL.md').write_bytes(('---\nname: demo-skill\ndescription: Demo\n---\n' + BODY).encode('utf-8'))
        (folder / 'refs' / 'notes.md').write_text('REFERENCED-ONLY-CONTENT', encoding='utf-8')
        self.dirs = {'demo-skill': folder}
        self.plan = q.prepare(make_suite(), self.dirs, 'test-model')

    def graded(self, **overrides):
        items = {job['id']: reply(job['id']) for job in self.plan['jobs']}
        items.update(overrides)
        return q.grade(self.plan, [item for item in items.values() if item is not None])

    def cases(self, report):
        return {case['id']: case for case in report['skills']['demo-skill']['cases']}

    def test_prepare_builds_private_blind_jobs(self):
        plan = self.plan
        self.assertEqual(q.digest(make_suite()), plan['suite_sha256'])
        self.assertEqual(plan['skills']['demo-skill']['files'], fingerprint(self.dirs['demo-skill']))
        self.assertEqual(['case-%d' % i for i in range(5)], [job['id'] for job in plan['jobs']])
        for job in plan['jobs']:
            self.assertEqual((job['model'], job['max_tokens'], job['timeout']), ('test-model', 1800, 120))
            self.assertIn('text-only simulation', job['task'])
            self.assertIn(BODY, job['context'])
            self.assertNotIn('REFERENCED-ONLY-CONTENT', job['context'])
            self.assertNotIn(SECRET_ANSWER, json.dumps(job))
            self.assertNotIn('"checks"', json.dumps(job))
            self.assertNotIn(self.temp.name, json.dumps(job))

    def test_prepare_rejects_unsafe_input(self):
        def broken(mutate):
            suite = make_suite()
            mutate(suite)
            with self.assertRaises(ValueError):
                q.prepare(suite, self.dirs, 'test-model')
        broken(lambda s: s.update(cases=[]))
        broken(lambda s: s['cases'].append(make_case(0)))
        broken(lambda s: s['cases'][0]['checks'][0].update(op='contains'))
        broken(lambda s: s['cases'][0]['checks'].append(dict(s['cases'][0]['checks'][0])))
        broken(lambda s: s['cases'][0]['checks'][0].update(path='unknown'))
        broken(lambda s: s['cases'][0].update(id='../escape'))
        broken(lambda s: s['cases'][0].update(skill='Bad Skill'))
        broken(lambda s: s['cases'][0]['response_schema'].update({'Bad Key': 'x'}))
        broken(lambda s: s['cases'][0].update(extra='field'))
        for model in ('', 'bad model', None):
            with self.assertRaises(ValueError):
                q.prepare(make_suite(), self.dirs, model)
        with self.assertRaises(ValueError):
            q.prepare(make_suite(), {}, 'test-model')
        with self.assertRaises(ValueError):
            q.prepare(make_suite(), {'demo-skill': self.root / 'missing'}, 'test-model')
        (self.root / 'empty').mkdir()
        (self.root / 'empty' / 'SKILL.md').write_text('---\nname: empty\ndescription: x\n---\n\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            q.prepare(make_suite(), {'demo-skill': self.root / 'empty'}, 'test-model')

    def test_prepare_rejects_linked_skill_folder(self):
        link = self.root / 'linked'
        try:
            link.symlink_to(self.dirs['demo-skill'], target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            q.prepare(make_suite(), {'demo-skill': link}, 'test-model')

    def test_all_correct_passes_and_report_is_clean(self):
        report = self.graded(**{'case-0': reply('case-0', elapsed_seconds=1.5, usage={'input_tokens': 10})})
        self.assertEqual(('text-simulation', 1, 'test-model'), (report['mode'], report['schema_version'], report['model']))
        self.assertEqual(self.plan['suite_sha256'], report['suite_sha256'])
        self.assertTrue(all(case['status'] == 'passed' for case in self.cases(report).values()))
        self.assertEqual(1.5, self.cases(report)['case-0']['elapsed_seconds'])
        self.assertEqual({'input_tokens': 10}, self.cases(report)['case-0']['usage'])
        self.assertNotIn('elapsed_seconds', self.cases(report)['case-1'])
        text = json.dumps(report)
        for private in (BODY.strip(), self.temp.name, 'REFERENCED-ONLY-CONTENT', 'Never invent'):
            self.assertNotIn(private, text)

    def test_wrong_values_fail_with_evidence(self):
        report = self.graded(**{'case-1': reply('case-1', {'line': 4, 'verified': False, 'label': SECRET_ANSWER})})
        case = self.cases(report)['case-1']
        self.assertEqual('failed', case['status'])
        self.assertEqual([False, True, True], [c['passed'] for c in case['checks']])
        self.assertEqual((3, 4), (case['checks'][0]['expected'], case['checks'][0]['actual']))

    def test_booleans_are_not_numbers(self):
        suite = make_suite(1)
        suite['cases'][0]['checks'] = [{'path': 'line', 'op': 'eq', 'value': 1},
                                       {'path': 'verified', 'op': 'eq', 'value': False}]
        plan = q.prepare(suite, self.dirs, 'test-model')
        report = q.grade(plan, [reply('case-0', {'line': True, 'verified': 0, 'label': 'x'})])
        self.assertEqual('failed', report['skills']['demo-skill']['cases'][0]['status'])
        self.assertEqual([False, False], [c['passed'] for c in report['skills']['demo-skill']['cases'][0]['checks']])
        self.assertFalse(q.strict_equal([0], [False]))
        self.assertTrue(q.strict_equal({'a': [1, None]}, {'a': [1.0, None]}))

    def test_response_defects_never_pass_and_keep_denominator(self):
        cases = {
            'case-0': None,
            'case-1': reply('case-1', model='other-model'),
            'case-2': reply('case-2', finish_reason='length'),
            'case-3': reply('case-3', '{"line": 3, "line": 3, "verified": false, "label": "%s"}' % SECRET_ANSWER),
            'case-4': reply('case-4', '```json\n%s\n```' % json.dumps(GOOD)),
        }
        report = self.graded(**cases)
        result = self.cases(report)
        self.assertEqual(5, len(result))
        for case in result.values():
            self.assertEqual('error', case['status'])
            self.assertTrue(case['error'])
            self.assertFalse(any(c['passed'] for c in case['checks']))
        self.assertIn('Missing', result['case-0']['error'])

    def test_more_defects(self):
        for override in ({'status': 'failed'}, {'truncated': True}, {'stop_reason': 'max_tokens'}):
            self.assertEqual('error', self.cases(self.graded(**{'case-0': reply('case-0', **override)}))['case-0']['status'])
        for answer in ({**GOOD, 'extra': 1}, {'line': 3, 'verified': False}, [GOOD], '', '{"line": NaN}'):
            item = reply('case-0', answer)
            self.assertEqual('error', self.cases(self.graded(**{'case-0': item}))['case-0']['status'], answer)
        report = q.grade(self.plan, [reply(job['id']) for job in self.plan['jobs']] + [reply('case-0')])
        self.assertEqual('error', self.cases(report)['case-0']['status'])
        self.assertEqual('Duplicate response', self.cases(report)['case-0']['error'])
        self.assertEqual('passed', self.cases(report)['case-1']['status'])

    def test_unknown_response_and_tampered_plan_fail_closed(self):
        with self.assertRaises(ValueError):
            q.grade(self.plan, [reply('not-a-case')])
        with self.assertRaises(ValueError):
            q.grade(self.plan, 'nope')
        tampered = copy.deepcopy(self.plan)
        tampered['suite']['cases'][0]['checks'][0]['value'] = 99
        with self.assertRaises(ValueError):
            q.grade(tampered, [])
        for mutate in (lambda p: p['jobs'].pop(), lambda p: p['jobs'][0].update(model='x'),
                       lambda p: p['skills'].clear(), lambda p: p.update(model='')):
            plan = copy.deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(ValueError):
                q.grade(plan, [])

    def test_assess_states(self):
        report = self.graded()
        files = fingerprint(self.dirs['demo-skill'])
        passed = q.assess(report, 'demo-skill', files)
        self.assertEqual(('passed', 5, 5, 'test-model', 'text-simulation'),
                         (passed['status'], passed['passed'], passed['total'], passed['model'], passed['mode']))
        self.assertEqual(files, passed['files'])
        self.assertEqual(q.digest(files), passed['version'])
        self.assertEqual('untested', q.assess(None, 'demo-skill', files)['status'])
        self.assertEqual('untested', q.assess(report, 'other-skill', files)['status'])
        self.assertEqual('stale', q.assess(report, 'demo-skill', {**files, 'SKILL.md': '0' * 64})['status'])
        self.assertEqual('stale', q.assess(report, 'demo-skill', None)['status'])
        failing = self.graded(**{'case-2': reply('case-2', {**GOOD, 'line': 9})})
        result = q.assess(failing, 'demo-skill', files)
        self.assertEqual(('failed', 4, 5), (result['status'], result['passed'], result['total']))
        self.assertEqual('failed', {c['id']: c for c in result['cases']}['case-2']['status'])
        with self.assertRaises(ValueError):
            q.assess(report, '../x', files)

    def test_assess_does_not_trust_claims(self):
        files = fingerprint(self.dirs['demo-skill'])
        report = self.graded(**{'case-0': reply('case-0', {**GOOD, 'line': 9})})

        def broken(mutate):
            bad = copy.deepcopy(report)
            mutate(bad)
            self.assertEqual('error', q.assess(bad, 'demo-skill', files)['status'])
        first = lambda r: r['skills']['demo-skill']['cases'][0]
        broken(lambda r: first(r)['checks'][0].update(passed=True))
        broken(lambda r: first(r).update(status='passed'))
        broken(lambda r: first(r)['checks'][0].update(actual=3))
        broken(lambda r: first(r).update(status='bogus'))
        broken(lambda r: first(r).update(checks=[]))
        broken(lambda r: r['skills']['demo-skill'].update(cases=[]))
        broken(lambda r: r['skills']['demo-skill'].update(files={'SKILL.md': 'short'}))
        broken(lambda r: r.update(mode='live'))
        broken(lambda r: r.update(schema_version=2))
        broken(lambda r: r.update(created_at='yesterday'))
        broken(lambda r: r.update(suite_sha256='abc'))
        broken(lambda r: r.update(skills={}))
        self.assertEqual('error', q.assess({'mode': 'text-simulation'}, 'demo-skill', files)['status'])
        self.assertEqual('error', q.assess([], 'demo-skill', files)['status'])

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = q.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_round_trip(self):
        base = Path(self.temp.name)
        suite = base / 'suite.json'
        suite.write_text(json.dumps(make_suite()), encoding='utf-8-sig')
        out = base / 'out'
        code, stdout, _ = self.run_cli('prepare', '--suite', str(suite), '--skill-root', str(self.root),
                                       '--model', 'test-model', '--output', str(out))
        self.assertEqual(0, code)
        self.assertEqual({'plan': 'plan.json', 'jobs': 5, 'batches': 2}, json.loads(stdout))
        batches = sorted(out.glob('gateway-batch-*.json'))
        self.assertEqual(['gateway-batch-1.json', 'gateway-batch-2.json'], [b.name for b in batches])
        sizes = [len(json.loads(b.read_text(encoding='utf-8'))['jobs']) for b in batches]
        self.assertEqual([4, 1], sizes)
        plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
        receipts = []
        for number, batch in enumerate(batches):
            jobs = json.loads(batch.read_text(encoding='utf-8'))['jobs']
            path = base / ('receipt-%d.json' % number)
            path.write_text(json.dumps({'jobs': [{**job, **reply(job['id'])} for job in jobs]}), encoding='utf-8-sig')
            receipts.append(path)
        report = base / 'report.json'
        args = ['grade', '--plan', str(out / 'plan.json'), '--output', str(report)]
        for path in receipts:
            args += ['--receipt', str(path)]
        self.assertEqual(0, self.run_cli(*args)[0])
        saved = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual(plan['suite_sha256'], saved['suite_sha256'])
        self.assertEqual('passed', q.assess(saved, 'demo-skill', fingerprint(self.dirs['demo-skill']))['status'])

        receipts[1].write_text(json.dumps({'jobs': []}), encoding='utf-8')
        code, stdout, _ = self.run_cli(*args)
        self.assertEqual((1, {'passed': 4, 'total': 5}), (code, json.loads(stdout)))

        receipts[1].write_text('{"jobs": [', encoding='utf-8')
        code, _, stderr = self.run_cli(*args)
        self.assertEqual(2, code)
        self.assertNotIn(BODY.strip(), stderr)
        code, _, stderr = self.run_cli('prepare', '--suite', str(suite), '--skill-root', str(base / 'nowhere'),
                                       '--model', 'test-model', '--output', str(base / 'out2'))
        self.assertEqual(2, code)
        self.assertNotIn(BODY.strip(), stderr)


if __name__ == '__main__':
    unittest.main()
