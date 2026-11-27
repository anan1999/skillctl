import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import skill_task as task
from skill_manage import main
from skillctl import fingerprint, write_json
from task_demo import fixture, GOOD, BAD, BASELINE


class TaskTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.app,self.spec,self.project,self.adapter=fixture(self.root)
        _,self.manager=self.app.manager('Demo')

    def plan(self,**kw):
        return task.prepare(self.manager,self.spec,self.project,'fixture',
                            kw.pop('names',['telemetry-contract','telemetry-legacy']),**kw)

    @staticmethod
    def answer(argv,job,timeout):
        code={'baseline':BASELINE,'telemetry-contract':GOOD,'telemetry-legacy':BAD}[job['id']]
        return {k:job[k] for k in ('id','model','request_sha256')} | {
            'status':'completed','answer':{'files':{'parser.py':code},'summary':'fixture'}}

    def run_trial(self,plan=None,answer=None,folder='trial',kind='fixture'):
        with patch('skill_task.call_adapter',side_effect=answer or self.answer) as call:
            report=task.run(self.manager,plan or self.plan(),{**self.adapter,'kind':kind},self.root/folder)
        return report,call

    def test_actual_checks_copies_evidence_and_unknown_usage(self):
        before=fingerprint(self.project)
        report,call=self.run_trial()
        self.assertTrue(report['ok']); self.assertEqual(call.call_count,3)
        self.assertEqual([r['status'] for r in report['variants']],['failed','passed','failed'])
        self.assertEqual(before,fingerprint(self.project)); self.assertFalse(self.manager.target.exists())
        self.assertIsNone(report['usage']['total_tokens']); self.assertIsNone(report['usage']['cost'])
        self.assertTrue(report['variants'][1]['skill_context_delivered'])
        self.assertIsNone(report['variants'][1]['native_harness_loading'])
        for invocation in call.call_args_list:
            job=invocation.args[1]
            self.assertEqual(job['model'],'fixture'); self.assertNotIn('verify.py',job['context'])
            self.assertNotIn('checks',json.loads(job['context']))
        task.evidence(report['report_path'])
        self.assertIn('return None',(self.root/'trial/telemetry-contract/changes.diff').read_text())
        with self.assertRaises(FileExistsError): self.run_trial()

    def test_candidates_are_unproven_lexical_matches(self):
        result=task.find(self.manager,self.spec,self.project)
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(not c['tested'] and c['matched_terms'] for c in result['candidates']))

    def test_guards_recipe_new_project_and_version_binding(self):
        report,_=self.run_trial()
        with self.assertRaises(ValueError): task.recipe(report['report_path'],'telemetry-legacy',self.root/'bad.json')
        saved=task.remember(self.manager,report['report_path'],'telemetry-legacy','sentinel')
        self.assertEqual(saved['guard']['adapter_kind'],'fixture')
        recipe=task.recipe(report['report_path'],'telemetry-contract',self.root/'recipe.json')['recipe']
        missing=copy.deepcopy(self.spec); missing['checks']=[c for c in missing['checks'] if c['id']!='sentinel']
        with self.assertRaisesRegex(ValueError,'removed or changed'):
            task.prepare(self.manager,missing,self.project,'fixture',['telemetry-contract'])
        second=self.root/'second'; shutil.copytree(self.project,second)
        write_json(second/'samples.json',{'readings':['125','-1.25']})
        plan=task.replay(self.manager,recipe,second)
        self.assertEqual(plan['model'],'fixture'); self.assertEqual(len(plan['guards']),1)
        replay,_=self.run_trial(plan,folder='replay')
        self.assertEqual(replay['variants'][1]['status'],'passed')
        (second/'verify.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'verification files'): task.replay(self.manager,recipe,second)
        skill=self.manager.sources['telemetry-contract']/'SKILL.md'
        skill.write_text(skill.read_text()+'\nNew version\n')
        with self.assertRaisesRegex(ValueError,'skill version'): task.replay(self.manager,recipe,self.project)

    def test_stale_preview_blocks_calls(self):
        for changed in ('project','skill','configuration'):
            with self.subTest(changed=changed):
                plan=self.plan()
                if changed=='project': path=self.project/'samples.json'
                elif changed=='skill': path=self.manager.sources['telemetry-contract']/'SKILL.md'
                else:
                    plan['binding']['target']='changed'; plan=task.seal({k:v for k,v in plan.items() if k!='sha256'})
                    path=None
                original=path.read_bytes() if path else None
                if path: path.write_bytes(original+b'\n')
                try:
                    with patch('skill_task.call_adapter') as call, self.assertRaises(ValueError):
                        task.run(self.manager,plan,self.adapter,self.root/'stale')
                    call.assert_not_called(); self.assertFalse((self.root/'stale').exists())
                finally:
                    if path: path.write_bytes(original)

    def test_invalid_and_truncated_answers_stop_without_retry(self):
        for mode in ('truncated','outside','receipt','timeout','invalid-json'):
            def answer(argv,job,timeout):
                r=self.answer(argv,job,timeout)
                if mode=='timeout': raise TimeoutError('bounded')
                if mode=='truncated': r['status']='truncated'
                if mode=='outside': r['answer']['files']={'verify.py':'print("fake")'}
                if mode=='receipt': r['request_sha256']='wrong'
                if mode=='invalid-json': r['answer']='{invalid'
                return r
            with self.subTest(mode=mode):
                report,call=self.run_trial(answer=answer,folder=mode)
                self.assertFalse(report['ok']); self.assertEqual(call.call_count,1)
                self.assertTrue(all(r['status']=='inconclusive' and not r['eligible_for_adoption'] for r in report['variants']))
                with self.assertRaises(ValueError): task.recipe(report['report_path'],'telemetry-contract',self.root/(mode+'.json'))

    def test_verifier_error_is_inconclusive(self):
        (self.project/'verify.py').write_text('raise RuntimeError("checker broken")\n')
        report,call=self.run_trial()
        self.assertFalse(report['ok']); self.assertEqual(call.call_count,1)
        self.assertEqual(report['variants'][0]['status'],'inconclusive')

    def test_verifier_timeout_is_bounded(self):
        (self.project/'verify.py').write_text('import time; time.sleep(10)\n')
        self.spec['checks'][0]['timeout']=1
        report,call=self.run_trial()
        self.assertFalse(report['ok']); self.assertIn('timeout',report['halted_reason'])
        self.assertEqual(call.call_count,1)

    def test_workspace_mutation_is_inconclusive(self):
        (self.project/'verify.py').write_text("import json; open('parser.py','w').write('tampered'); print(json.dumps({'passed':True}))\n")
        report,call=self.run_trial()
        self.assertFalse(report['ok']); self.assertIn('changed workspace',report['halted_reason'])
        self.assertEqual(call.call_count,1)

    def test_generated_bytecode_is_evidence_and_cannot_be_hidden(self):
        (self.project/'verify.py').write_text("import json; open('hidden.pyc','wb').write(b'changed'); print(json.dumps({'passed':True}))\n")
        report,call=self.run_trial()
        self.assertFalse(report['ok']); self.assertIn('changed workspace',report['halted_reason'])
        self.assertEqual(call.call_count,1)

    def test_trial_evidence_has_a_size_bound(self):
        folder=self.root/'oversized'; folder.mkdir()
        (folder/'large').write_bytes(b'x'*(2*1024*1024+1))
        with self.assertRaisesRegex(ValueError,'size limits'): task.fingerprint(folder)

    def test_each_regression_visible_and_fixture_cannot_adopt(self):
        def answer(argv,job,timeout):
            r=self.answer(argv,job,timeout)
            if job['id']=='baseline': r['answer']['files']['parser.py']=GOOD
            return r
        report,_=self.run_trial(answer=answer)
        self.assertEqual(report['variants'][2]['regressions'],['sentinel'])
        with self.assertRaisesRegex(ValueError,'nonfixture'):
            task.adopt_preview(self.app,report['report_path'],'telemetry-contract','Demo')

    def test_model_report_only_previews_install_and_evidence_drift_blocks(self):
        # Mock responses test eligibility mechanics, never claim live model evidence.
        report,_=self.run_trial(kind='model')
        preview=task.adopt_preview(self.app,report['report_path'],'telemetry-contract','Demo')
        self.assertTrue(preview['ok']); self.assertIsNotNone(preview['plan_id'])
        self.assertFalse(self.manager.target.exists())
        (self.root/'trial/telemetry-contract/sentinel.out').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            task.adopt_preview(self.app,report['report_path'],'telemetry-contract','Demo')

    def test_last_call_source_change_blocks_adoption(self):
        def answer(argv,job,timeout):
            r=self.answer(argv,job,timeout)
            if job['id']=='telemetry-legacy':
                (self.project/'samples.json').write_text('{"readings":[]}')
            return r
        report,_=self.run_trial(answer=answer,kind='model')
        self.assertFalse(report['ok']); self.assertFalse(report['original_unchanged'])
        self.assertFalse(any(r['eligible_for_adoption'] for r in report['variants']))

    def test_limits_paths_and_protected_verifiers(self):
        with self.assertRaisesRegex(ValueError,'max_calls'): self.plan(max_calls=2)
        for change in ('../escape.py','verify.py','parser.py'):
            spec=copy.deepcopy(self.spec)
            if change=='../escape.py': spec['files'].append(change)
            elif change=='verify.py': spec['write_files'].append(change)
            else: spec['checks'][0]['argv'][1]=change
            with self.subTest(change=change), self.assertRaises(ValueError): task.spec_check(spec)

    def cli(self,*args):
        output=io.StringIO()
        with redirect_stdout(output): status=main(['--config',str(self.root/'config.json'),'task',*map(str,args),'--target','Demo'])
        return status,json.loads(output.getvalue())

    def test_cli_full_flow_uses_real_fixture_subprocess(self):
        status,found=self.cli('find','--spec',self.root/'spec.json','--project',self.project)
        self.assertEqual(status,0); self.assertEqual(len(found['candidates']),2)
        status,p=self.cli('prepare','--spec',self.root/'spec.json','--project',self.project,'--model','fixture','--output',self.root/'preview')
        self.assertEqual(status,0); self.assertEqual(p['planned_calls'],3)
        status,r=self.cli('run','--plan',p['plan'],'--adapter',self.root/'adapter.json','--output',self.root/'cli-run')
        self.assertEqual(status,0); self.assertTrue(r['ok'])
        status,g=self.cli('remember','--report',r['report_path'],'--candidate','telemetry-legacy','--check','sentinel')
        self.assertEqual(status,0)
        status,recipe=self.cli('recipe','--report',r['report_path'],'--candidate','telemetry-contract','--output',self.root/'saved.json')
        self.assertEqual(status,0)
        status,replay=self.cli('replay','--recipe',self.root/'saved.json','--project',self.project,'--output',self.root/'replay-preview')
        self.assertEqual(status,0); self.assertEqual(replay['remembered_checks'],['sentinel'])


if __name__=='__main__': unittest.main()
