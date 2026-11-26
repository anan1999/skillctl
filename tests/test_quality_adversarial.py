"""Coordinator-owned adversarial checks beyond the model's happy path."""
import copy
import json
import unittest
import test_quality_acceptance as fixture
import skill_quality as q


class QualityAdversarial(unittest.TestCase):
    setUp = fixture.QualityAcceptance.setUp
    tearDown = fixture.QualityAcceptance.tearDown
    assess = fixture.QualityAcceptance.assess
    def test_json_duplicate_fields_extra_fields_and_nonfinite(self):
        for answer in (
            '{"first_error_line":99,"first_error_line":3,"cause_kind":"config_parse","proposed_actions":["read_log_excerpt","inspect_named_config"],"root_cause_verified":false}',
            json.dumps({**json.loads(self.responses[0]['answer']), 'unexpected': 'extra'}),
            self.responses[0]['answer'].replace('3', 'NaN', 1),
        ):
            bad=copy.deepcopy(self.responses);bad[0]['answer']=answer
            self.assertNotEqual(self.assess(q.grade(self.plan,bad))['status'],'passed')

    def test_tampered_plan_and_report_claims(self):
        plan=copy.deepcopy(self.plan);plan['suite']['cases'][0]['checks'][0]['value']=99
        with self.assertRaises(ValueError):q.grade(plan,self.responses)
        report=q.grade(self.plan,self.responses)
        check=report['skills']['failure-triage']['cases'][0]['checks'][0]
        check['actual']=99;check['passed']=True
        self.assertNotEqual(self.assess(report)['status'],'passed')

    def test_reference_files_part_of_version(self):
        report=q.grade(self.plan,self.responses)
        (self.dirs['failure-triage']/'extra.txt').write_text('added reference',encoding='utf-8')
        self.assertEqual(self.assess(report)['status'],'stale')

    def test_receipt_bound_to_exact_prompt_and_missing_status_rejected(self):
        jobs = [{**job, **response} for job, response in zip(self.plan['jobs'], self.responses)]
        report = q.grade(self.plan, q.bind_responses(self.plan, jobs))
        self.assertEqual(report['request_binding'], 'matched')
        self.assertEqual(self.assess(report)['status'], 'passed')
        old = copy.deepcopy(jobs); old[0]['context'] += '\nDifferent skill version'
        with self.assertRaises(ValueError): q.bind_responses(self.plan, old)
        with self.assertRaises(ValueError): q.bind_responses(self.plan, self.responses)
        bad = copy.deepcopy(self.responses); bad[0].pop('status')
        self.assertNotEqual(self.assess(q.grade(self.plan, bad))['status'], 'passed')
        bound = q.bind_responses(self.plan, jobs); bound[0]['request_sha256'] = '0' * 64
        self.assertNotEqual(self.assess(q.grade(self.plan, bound))['status'], 'passed')
