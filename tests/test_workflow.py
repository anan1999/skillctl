import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from skillctl import Manager, fingerprint, write_json
from skill_workflow import (record_learning, learning_receipts, impact, prepare_comparison,
                            run_comparison, read_object, digest)

REPO = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.old = self.root/'old/example-skill'; self.old.mkdir(parents=True)
        self.new = self.root/'new/example-skill'; self.new.mkdir(parents=True)
        self.header = '---\nname: example-skill\ndescription: Diagnose clock evidence\n---\n'
        (self.old/'SKILL.md').write_text(self.header+'Write the clock.\n', encoding='utf-8')
        (self.new/'SKILL.md').write_text(self.header+'Read only.\n', encoding='utf-8')
        self.manager = Manager(self.root/'new', self.root/'target', self.root/'state')
        self.suite = {'schema_version':1, 'id':'compare-example', 'cases':[{
            'id':'readonly', 'skill':'example-skill', 'kind':'authorization', 'task':'Check clock without changes',
            'input':'Time evidence is present.', 'response_schema':{'action':'read or write'},
            'checks':[{'path':'action', 'op':'eq', 'value':'read'}]}]}
        self.adapter_script = self.root/'adapter.py'
        self.adapter_script.write_text('''import json,sys
r=json.load(sys.stdin)
print(json.dumps(dict(id=r['id'],model=r['model'],request_sha256=r['request_sha256'],status='completed',
 answer={'action':'read' if 'Read only.' in r['context'] else 'write'})))
''', encoding='utf-8')
        self.adapter = {'argv':[sys.executable,'-B',str(self.adapter_script)],'kind':'fixture'}
        self.config = self.root/'config.json'
        write_json(self.config, {'schema_version':1,'repo':str(REPO),'source':str(self.root/'new'),
            'state':str(self.root/'state'),'targets':{'Test':str(self.root/'target')}})

    def plan(self, **kwargs):
        return prepare_comparison(self.suite,self.old,self.new,'fixture',**kwargs)

    def receipt_input(self):
        return {'schema_version':1,'skill':'example-skill','scope':{'kind':'project','path':str(self.root)},
            'trigger':'唯讀時間診斷','lesson':'Preserve read-only intent','decision':'update','reason':'Existing skill covers clocks',
            'evidence':[{'path':str(self.new/'SKILL.md'),'summary':'Candidate wording; not a behavioral test'}]}

    def test_learning_records_evidence_not_claimed_success(self):
        before=fingerprint(self.root/'new')
        with patch('socket.socket',side_effect=AssertionError('network not allowed')):
            result=record_learning(self.manager,self.receipt_input())
        self.assertEqual(result['receipt']['status'],'observed')
        self.assertFalse(result['receipt']['installed'])
        self.assertEqual(before,fingerprint(self.root/'new'))
        self.assertFalse((self.root/'target').exists())
        rows=learning_receipts(self.manager)['receipts']
        self.assertEqual(rows[0]['evidence_now'][0]['status'],'matched')
        (self.new/'SKILL.md').write_text(self.header+'Changed',encoding='utf-8')
        self.assertEqual(learning_receipts(self.manager)['receipts'][0]['evidence_now'][0]['status'],'changed')
        (self.new/'SKILL.md').unlink()
        self.assertEqual(learning_receipts(self.manager)['receipts'][0]['evidence_now'][0]['status'],'unavailable')

    def test_learning_rejects_bad_scope_missing_evidence_and_tampering(self):
        value=self.receipt_input(); value['scope']={'kind':'project','path':'does-not-exist'}
        with self.assertRaises(ValueError): record_learning(self.manager,value)
        value=self.receipt_input(); value['evidence']=[]
        with self.assertRaises(ValueError): record_learning(self.manager,value)
        value['decision']='skip'
        result=record_learning(self.manager,value)
        self.assertEqual(result['receipt']['status'],'skipped')
        receipt=read_object(result['path']); receipt['lesson']='edited'
        write_json(Path(result['path']),receipt)
        with self.assertRaisesRegex(ValueError,'integrity'): learning_receipts(self.manager)

    def test_learning_update_requires_existing_skill_create_cannot_duplicate(self):
        value=self.receipt_input(); value['decision']='create'
        with self.assertRaises(ValueError): record_learning(self.manager,value)
        value['decision']='update'; value['skill']='unknown-skill'
        with self.assertRaises(ValueError): record_learning(self.manager,value)

    def test_impact_preserves_local_files_and_explains_block(self):
        self.manager.install('example-skill')
        installed=self.root/'target/example-skill/SKILL.md'
        installed.write_text(self.header+'Read only.\nKeep local guidance.\n',encoding='utf-8')
        before=fingerprint(self.root)
        result=impact(self.manager,'example-skill')
        self.assertEqual(result['status'],'local-drift')
        self.assertTrue(result['blocked_reason'])
        self.assertTrue(result['files'][0]['review_removed_guidance'])
        self.assertIn('Keep local guidance.',result['files'][0]['removed_excerpt'])
        self.assertFalse(result['applied'])
        self.assertEqual(before,fingerprint(self.root))

    def test_budget_preflight_stale_plan_and_limit_tampering(self):
        self.suite['cases'].append({**self.suite['cases'][0],'id':'second'})
        with self.assertRaisesRegex(ValueError,'max_calls'): self.plan(max_calls=2)
        plan=self.plan(max_calls=4)
        plan['versions']['old']['jobs'][0]['max_tokens']=4096
        plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
        with self.assertRaises(ValueError): run_comparison(plan,self.adapter,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
        plan=self.plan()
        (self.new/'SKILL.md').write_text(self.header+'Changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'changed'): run_comparison(plan,self.adapter,self.root/'stale')

    def test_comparison_runs_pairs_unknown_cost_and_refuses_same_run(self):
        plan=self.plan(max_calls=2)
        result=run_comparison(plan,self.adapter,self.root/'run')
        self.assertTrue(result['ok']); self.assertTrue(result['is_fixture'])
        self.assertEqual(result['attempted_calls'],2)
        self.assertEqual(result['pairs'][0]['verdict'],'improved')
        self.assertEqual(result['candidate_assessment'],'passed_checks')
        self.assertIsNone(result['usage']['total_tokens']); self.assertIsNone(result['usage']['cost'])
        with self.assertRaises(FileExistsError): run_comparison(plan,self.adapter,self.root/'run')

    def test_failure_is_inconclusive_and_never_retried(self):
        self.adapter_script.write_text('import sys; sys.exit(2)',encoding='utf-8')
        result=run_comparison(self.plan(),self.adapter,self.root/'failed')
        self.assertFalse(result['ok']); self.assertEqual(result['attempted_calls'],1)
        self.assertEqual(result['pairs'][0]['verdict'],'inconclusive')
        self.assertEqual(result['candidate_assessment'],'inconclusive')
        self.assertIsNone(result['usage']['total_tokens'])

    def test_timeout_and_bad_request_binding(self):
        self.adapter_script.write_text('import time; time.sleep(10)',encoding='utf-8')
        result=run_comparison(self.plan(timeout=1),self.adapter,self.root/'timeout')
        self.assertEqual(result['attempted_calls'],1); self.assertIn('timeout',result['halted_reason'])
        self.adapter_script.write_text('print(\'{"id":"wrong"}\')',encoding='utf-8')
        result=run_comparison(self.plan(),self.adapter,self.root/'wrong')
        self.assertEqual(result['attempted_calls'],1); self.assertIn('match',result['halted_reason'])

    def test_regression_is_visible_even_when_comparison_completes(self):
        plan=prepare_comparison(self.suite,self.new,self.old,'fixture')
        result=run_comparison(plan,self.adapter,self.root/'regression')
        self.assertTrue(result['ok']); self.assertEqual(result['pairs'][0]['verdict'],'regressed')
        self.assertEqual(result['candidate_assessment'],'regressed')

    def test_higher_total_cannot_hide_regression_and_two_failures_are_not_pass(self):
        self.suite['cases']=[{**self.suite['cases'][0],'id':name} for name in ('critical','improve-a','improve-b')]
        self.adapter_script.write_text('''import json,sys
r=json.load(sys.stdin)
new='Read only.' in r['context']
passed=(not new) if r['id']=='critical' else new
print(json.dumps(dict(id=r['id'],model=r['model'],request_sha256=r['request_sha256'],status='completed',
 answer={'action':'read' if passed else 'write'})))
''',encoding='utf-8')
        result=run_comparison(self.plan(),self.adapter,self.root/'net-improvement')
        self.assertTrue(result['ok'])
        self.assertEqual([p['verdict'] for p in result['pairs']],['regressed','improved','improved'])
        self.assertEqual(result['candidate_assessment'],'regressed')
        self.adapter_script.write_text('''import json,sys
r=json.load(sys.stdin)
print(json.dumps(dict(id=r['id'],model=r['model'],request_sha256=r['request_sha256'],status='completed',answer={'action':'write'})))
''',encoding='utf-8')
        result=run_comparison(self.plan(),self.adapter,self.root/'both-failed')
        self.assertTrue(result['ok'])
        self.assertEqual(result['candidate_assessment'],'failed_checks')

    def test_hidden_answers_context_binding_and_input_bound(self):
        plan=self.plan()
        self.assertNotIn('checks',plan['versions']['new']['jobs'][0]['context'])
        plan['versions']['old']['jobs'][0]['context']='Pretend this is the new skill'
        plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
        with self.assertRaisesRegex(ValueError,'requests differ'): run_comparison(plan,self.adapter,self.root/'changed-context')
        (self.new/'SKILL.md').write_text(self.header+'x'*140000,encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'128 KiB'): self.plan()

    def test_truncation_stops_and_total_budget_does_not_launch_second_call(self):
        self.adapter_script.write_text('''import json,sys
r=json.load(sys.stdin)
print(json.dumps(dict(id=r['id'],model=r['model'],request_sha256=r['request_sha256'],status='completed',
 finish_reason='length', answer={'action':'read'})))
''',encoding='utf-8')
        result=run_comparison(self.plan(),self.adapter,self.root/'truncated')
        self.assertFalse(result['ok']); self.assertEqual(result['attempted_calls'],1)
        self.assertEqual(result['pairs'][0]['verdict'],'inconclusive')
        self.adapter_script.write_text('import time; time.sleep(5)',encoding='utf-8')
        result=run_comparison(self.plan(timeout=10,total_timeout=1),self.adapter,self.root/'total-timeout')
        self.assertFalse(result['ok']); self.assertEqual(result['attempted_calls'],1)

    def test_cli_entrypoint_receipt_impact_prepare_and_run(self):
        def cli(*args):
            process=subprocess.run([sys.executable,'-B','-X','utf8',str(REPO/'skills/skillctl-manager/scripts/manage.py'),
                '--repo',str(REPO),'--config',str(self.config),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)
            return json.loads(process.stdout)
        input_file=self.root/'learn.json'; write_json(input_file,self.receipt_input())
        receipt=cli('learn','record','--input',input_file,'--target','Test')
        self.assertEqual(len(cli('learn','show',receipt['receipt']['id'],'--target','Test')['receipts']),1)
        self.assertFalse(cli('impact','example-skill','--target','Test')['applied'])
        suite=self.root/'suite.json';write_json(suite,self.suite)
        plan=cli('compare','prepare','--suite',suite,'--old',self.old,'--new',self.new,'--model','fixture','--output',self.root/'plan')
        adapter=self.root/'adapter.json';write_json(adapter,self.adapter)
        result=cli('compare','run','--plan',plan['plan'],'--adapter',adapter,'--output',self.root/'cli-run')
        self.assertEqual(result['pairs'][0]['verdict'],'improved')


if __name__ == '__main__': unittest.main()
