import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from skill_manage import Management, load_config
from skillctl import Manager, fingerprint, write_json
from skill_quality import prepare, grade

REPO = Path(__file__).resolve().parents[1]


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        self.skill = self.source/'example-skill'; self.skill.mkdir(parents=True)
        (self.skill/'SKILL.md').write_text('---\nname: example-skill\ndescription: 相機時間診斷\n---\nRead only.',encoding='utf-8')
        (self.skill/'notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nlast\n')
        self.codex = self.root/'user/.codex/skills'
        self.pi = self.root/'project/.pi/skills'
        self.state = self.root/'state'
        self.home = self.root/'logs'; (self.home/'sessions').mkdir(parents=True)
        self.usage = self.root/'reports/usage.json'
        self.quality = self.root/'reports/quality.json'
        case=json.loads((REPO/'quality/pilot.json').read_text(encoding='utf-8'))['cases'][0]
        case['skill']='example-skill'
        suite=dict(schema_version=1,id='test',cases=[case])
        plan=prepare(suite,{'example-skill':self.skill},'synthetic')
        write_json(self.quality,grade(plan,[dict(id=case['id'],model='synthetic',status='completed',
              answer=json.dumps({c['path']:c['value'] for c in case['checks']}))]))
        self.config=self.root/'manager.json'
        write_json(self.config,dict(schema_version=1,repo=str(REPO),source=str(self.source),state=str(self.state),
              targets={'Codex':str(self.codex),'Pi':str(self.pi)},codex_home=str(self.home),
              usage_report=str(self.usage),quality_report=str(self.quality)))
        self.app=Management(load_config(self.config))

    def install(self, target='Codex'):
        result=self.app.preview('install','example-skill',target)
        self.assertTrue(result['plan_id'])
        return self.app.apply(result['plan_id'],target)

    def test_queries_no_network_or_mutation_and_quality_versions(self):
        self.install()
        before=fingerprint(self.root)
        with patch('socket.socket',side_effect=AssertionError('No network/listener allowed')):
            self.assertFalse(self.app.locations()['service_required'])
            rows=self.app.listing('Codex','時間')['skills']
            self.assertEqual([r['name'] for r in rows],['example-skill'])
            self.assertEqual(self.app.quality('example-skill','Codex')['skills']['example-skill']['status'],'passed')
            self.assertTrue(self.app.doctor('example-skill','Codex')['skills'])
            self.assertEqual(self.app.usage('example-skill',7,'Pi')['skills']['example-skill']['status'],'unsupported')
        self.assertEqual(before,fingerprint(self.root))
        (self.codex/'example-skill/notes.txt').write_text('local')
        self.assertEqual(self.app.quality('example-skill','Codex')['skills']['example-skill']['status'],'stale')
        self.app.config['quality_report']=None
        self.assertEqual(self.app.quality('example-skill','Codex')['skills']['example-skill']['status'],'untested')

    def test_refresh_usage_two_windows_and_missing_data(self):
        with self.assertRaises(FileNotFoundError):self.app.usage(days=7)
        stamp=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        rows=[dict(type='response_item',timestamp=stamp,payload=dict(type='function_call',call_id='read',
                   name='exec_command',arguments=json.dumps({'cmd':'cat /skills/example-skill/SKILL.md'}))),
              dict(type='response_item',timestamp=stamp,payload=dict(type='function_call_output',call_id='read',
                   output=json.dumps({'exit_code':0,'output':(self.skill/'SKILL.md').read_text(encoding='utf-8')})))]
        log=self.home/'sessions/test.jsonl';log.write_text('\n'.join(map(json.dumps,rows)),encoding='utf-8')
        before=log.read_bytes()
        with patch('socket.socket',side_effect=AssertionError('No network')):
            first=self.app.usage('example-skill',7,refresh=True)
            month=self.app.usage('example-skill',30)
        self.assertEqual(first['skills']['example-skill']['reads'],1)
        self.assertEqual(month['skills']['example-skill']['conversations'],1)
        self.assertEqual(log.read_bytes(),before)
        self.usage.write_text('{broken')
        with self.assertRaises(ValueError):self.app.usage()
        self.app.config['usage_report']=None
        self.assertEqual(self.app.usage('example-skill')['skills']['example-skill']['status'],'unavailable')

    def test_preview_install_update_rollback_are_bound_to_target(self):
        before=fingerprint(self.source)
        plan=self.app.preview('install','example-skill','Pi')
        self.assertFalse((self.pi/'example-skill').exists())
        self.assertEqual(before,fingerprint(self.source))
        self.app.apply(plan['plan_id'],'Pi')
        self.assertEqual(fingerprint(self.skill),fingerprint(self.pi/'example-skill'))
        self.assertFalse(self.codex.exists())
        old=fingerprint(self.pi/'example-skill')
        (self.skill/'notes.txt').write_text('new upstream')
        update=self.app.preview('update','example-skill','Pi')
        self.app.apply(update['plan_id'],'Pi')
        self.assertEqual(fingerprint(self.skill),fingerprint(self.pi/'example-skill'))
        rollback=self.app.preview('rollback','example-skill','Pi')
        self.app.apply(rollback['plan_id'],'Pi')
        self.assertEqual(old,fingerprint(self.pi/'example-skill'))
        self.assertTrue(self.app.doctor('example-skill','Pi')['skills'])

    def test_stale_expired_wrong_target_and_reuse_refused(self):
        plan=self.app.preview('install','example-skill','Codex')
        with self.assertRaises(FileNotFoundError):self.app.apply(plan['plan_id'],'Pi')
        (self.skill/'notes.txt').write_text('changed after preview')
        with self.assertRaisesRegex(ValueError,'stale'):self.app.apply(plan['plan_id'],'Codex')
        self.assertFalse((self.codex/'example-skill').exists())
        with self.assertRaises(FileNotFoundError):self.app.apply(plan['plan_id'],'Codex')
        plan=self.app.preview('install','example-skill','Codex')
        with patch('skill_manage.time.time',return_value=10**12):
            with self.assertRaisesRegex(ValueError,'expired'):self.app.apply(plan['plan_id'],'Codex')
        self.app.apply(plan['plan_id'],'Codex')
        with self.assertRaises(FileNotFoundError):self.app.apply(plan['plan_id'],'Codex')

    def test_local_edits_block_update_and_keep_preserves_bytes(self):
        self.install()
        (self.codex/'example-skill/notes.txt').write_text('MY LOCAL CHANGE')
        (self.skill/'notes.txt').write_text('NEW SOURCE')
        before=fingerprint(self.codex)
        result=self.app.preview('update','example-skill','Codex')
        self.assertFalse(result['ok']);self.assertIsNone(result['plan_id'])
        self.assertIn('Local drift',result['error']);self.assertTrue(result['comparison'])
        merge=self.app.preview('merge','example-skill','Codex')
        self.assertFalse(merge['ok']);self.assertIsNone(merge['plan_id'])
        keep=self.app.preview('keep','example-skill','Codex')
        self.app.apply(keep['plan_id'],'Codex')
        self.assertEqual(before,fingerprint(self.codex))
        self.assertFalse(self.app.preview('update','example-skill','Codex')['ok'])

    def test_adopt_then_nonoverlapping_merge(self):
        shutil.copytree(self.skill,self.codex/'example-skill')
        before=fingerprint(self.codex)
        adopt=self.app.preview('adopt','example-skill','Codex')
        self.app.apply(adopt['plan_id'],'Codex')
        self.assertEqual(before,fingerprint(self.codex))
        (self.codex/'example-skill/notes.txt').write_text('LOCAL\ntwo\nthree\nfour\nfive\nsix\nlast\n')
        (self.skill/'notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nUPSTREAM\n')
        merge=self.app.preview('merge','example-skill','Codex')
        self.assertTrue(merge['ok'])
        self.app.apply(merge['plan_id'],'Codex')
        text=(self.codex/'example-skill/notes.txt').read_text()
        self.assertIn('LOCAL',text);self.assertIn('UPSTREAM',text)

    def test_config_change_and_traversal_fail_without_mutation(self):
        plan=self.app.preview('install','example-skill','Codex')
        self.app.config['source']=self.root/'different-source';self.app.config['source'].mkdir()
        with self.assertRaisesRegex(ValueError,'configuration'):self.app.apply(plan['plan_id'],'Codex')
        self.assertFalse(self.codex.exists())
        with self.assertRaises(ValueError):self.app.apply('../../escape','Codex')
        with self.assertRaises(ValueError):self.app.listing()
        with self.assertRaises(ValueError):self.app.preview('install','../escape','Codex')
        config=json.loads(self.config.read_text())
        config['targets']['Codex']='relative'
        write_json(self.config,config)
        with self.assertRaises(ValueError):load_config(self.config)

    def test_installed_entrypoint_runs_from_other_working_directory(self):
        installed=self.root/'isolated/.agents/skills/skillctl-manager'
        Manager(REPO,installed.parent,self.root/'bootstrap-state').install('skillctl-manager')
        self.assertEqual(fingerprint(installed),fingerprint(REPO/'skills/skillctl-manager'))
        command=[sys.executable,'-B','-X','utf8',str(installed/'scripts/manage.py'),'--config',str(self.config)]
        result=subprocess.run([*command,'locations'],cwd=self.root,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(json.loads(result.stdout)['service_required'])
        result=subprocess.run([*command,'preview','install','example-skill','--target','Pi'],
                              cwd=self.root,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        plan=json.loads(result.stdout)
        result=subprocess.run([*command,'apply',plan['plan_id'],'--target','Pi'],
                              cwd=self.root,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(fingerprint(self.skill),fingerprint(self.pi/'example-skill'))


if __name__=='__main__':
    unittest.main()
