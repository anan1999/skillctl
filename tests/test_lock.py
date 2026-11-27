import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import skill_lock as lock
from skillctl import Manager, fingerprint, write_json
from skill_manage import main

REPO = Path(__file__).resolve().parents[1]


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lt-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root/'source'
        for name in ('alpha', 'beta'):
            folder = self.source/name
            folder.mkdir(parents=True)
            (folder/'SKILL.md').write_bytes(f'---\nname: {name}\ndescription: Example\n---\nDo not execute devices.\n'.encode())
            (folder/'notes.txt').write_bytes(b'one\ntwo\nthree\nfour\nfive\nsix\nseven\n')
        (self.source/'alpha/binary.bin').write_bytes(bytes([0, 255, 42]))
        self.a = Manager(self.source, self.root/'a/skills', self.root/'state-a')
        self.b_source = self.root/'empty'; self.b_source.mkdir()
        self.b = Manager(self.b_source, self.root/'b/skills', self.root/'state-b')
        self.file = self.root/'project/skillctl.lock.json'
        self.a.install('alpha'); self.a.install('beta')

    def create(self, names=()):
        preview = lock.create_preview(self.a, self.file, names)
        if preview['plan_id']:
            lock.apply(self.a, preview['plan_id'])
        return preview

    def restore(self, manager=None, keep=False):
        manager = manager or self.b
        result = lock.restore_preview(manager, self.file, keep)
        if result['plan_id']:
            return lock.apply(manager, result['plan_id'])
        return result

    def test_two_machine_offline_restore_pins_exact_bytes_and_rollback(self):
        before = fingerprint(self.a.target)
        preview = lock.create_preview(self.a, self.file)
        self.assertFalse(self.file.exists())
        lock.apply(self.a, preview['plan_id'])
        self.assertEqual(fingerprint(self.a.target), before)
        raw = self.file.read_bytes()
        self.assertNotIn(str(self.root).encode(), raw)
        # The originating source and machine can disappear entirely.
        shutil.move(self.source, self.root/'source-away')
        shutil.move(self.a.target, self.root/'machine-a-away')
        with (patch('socket.socket', side_effect=AssertionError('No network')),
              patch('subprocess.run', side_effect=AssertionError('No subprocess during restore'))):
            result = self.restore()
        self.assertTrue(result['matches_lock'])
        self.assertEqual(fingerprint(self.b.target), before)
        self.assertTrue(lock.check(self.b, self.file)['ok'])
        self.assertEqual(lock.capture(self.b, ())[0], lock.load(self.file)[0])
        self.assertIsNone(lock.restore_preview(self.b, self.file)['plan_id'])
        self.b.rollback('alpha')
        self.assertFalse((self.b.target/'alpha').exists())
        self.assertTrue((self.b.target/'beta').exists())

    def test_installed_revision_survives_source_moving_forward(self):
        def git(*args):
            result = subprocess.run(['git','-C',str(self.source),*args],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout.strip()
        git('init'); git('add','.'); git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','v1')
        old = git('rev-parse','HEAD')
        # Another isolated install has a real Git revision in its receipt.
        self.a = Manager(self.source,self.root/'git-target',self.root/'git-state')
        self.a.install('alpha')
        (self.source/'alpha/notes.txt').write_bytes(b'new upstream version\n')
        git('add','.'); git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','v2')
        self.create(['alpha'])
        value,_,_=lock.load(self.file)
        self.assertEqual(value['skills'][0]['source_revision']['commit'],old)
        self.restore()
        self.assertEqual(self.b.db()['installed']['alpha']['source_revision']['commit'],old)
        self.assertNotEqual(fingerprint(self.b.target/'alpha'),fingerprint(self.source/'alpha'))

    def test_carried_customizations_stay_protected_after_restore(self):
        custom=self.a.target/'alpha/notes.txt'
        custom.write_bytes(b'MY IMPORTANT RULE\n')
        self.create(['alpha'])
        value,_,_=lock.load(self.file)
        self.assertTrue(lock.summary(value)[0]['customized'])
        self.restore()
        self.assertEqual((self.b.target/'alpha/notes.txt').read_bytes(),custom.read_bytes())
        normal = Manager(self.source,self.b.target,self.root/'state-b')
        with self.assertRaisesRegex(ValueError,'customizations'):
            normal.install('alpha')
        self.assertEqual(normal.inspect('alpha',normal.db())['status'],'customized')

    def test_local_drift_blocks_whole_batch_then_keep_skips_without_receipt_change(self):
        self.b = Manager(self.source,self.b.target,self.root/'state-b')
        self.b.install('alpha')
        (self.b.target/'alpha/notes.txt').write_bytes(b'LOCAL ON MACHINE B\n')
        original = copy.deepcopy(self.b.db()['installed']['alpha'])
        before = fingerprint(self.b.target)
        self.create()
        blocked = lock.restore_preview(self.b,self.file)
        self.assertFalse(blocked['ok']); self.assertIsNone(blocked['plan_id'])
        self.assertEqual(fingerprint(self.b.target),before)
        self.assertFalse((self.b.target/'beta').exists())
        result = self.restore(keep=True)
        self.assertTrue(result['ok']); self.assertFalse(result['matches_lock'])
        self.assertEqual(result['kept_local'],['alpha'])
        self.assertEqual((self.b.target/'alpha/notes.txt').read_bytes(),b'LOCAL ON MACHINE B\n')
        self.assertEqual(self.b.db()['installed']['alpha'],original)
        self.assertTrue((self.b.target/'beta').exists())
        self.assertFalse(lock.check(self.b,self.file)['ok'])

    def test_clean_managed_update_and_unlisted_skill_untouched(self):
        self.create(['alpha']); self.restore()
        extra=self.b.target/'extra'; extra.mkdir(); (extra/'note').write_bytes(b'private')
        old=fingerprint(self.b.target/'alpha')
        (self.source/'alpha/notes.txt').write_bytes(b'v2\n')
        self.a.install('alpha'); self.create(['alpha'])
        self.assertEqual(self.restore()['changed'],['alpha'])
        self.assertEqual((extra/'note').read_bytes(),b'private')
        self.b.rollback('alpha')
        self.assertEqual(fingerprint(self.b.target/'alpha'),old)

    def test_unmanaged_identical_copy_not_silently_adopted(self):
        shutil.copytree(self.source/'alpha',self.b.target/'alpha')
        self.create(['alpha'])
        self.assertFalse(lock.restore_preview(self.b,self.file)['ok'])
        kept=lock.restore_preview(self.b,self.file,True)
        self.assertTrue(kept['ok']); self.assertIsNone(kept['plan_id'])
        self.assertTrue(kept['matches_lock'])
        self.assertFalse(kept['skills'][0]['managed'])
        self.assertFalse(self.b.db_path.exists())

    def test_local_deletion_is_preserved_and_reported_missing(self):
        self.create(['alpha']); self.restore()
        shutil.rmtree(self.b.target/'alpha')
        self.assertFalse(lock.restore_preview(self.b,self.file)['ok'])
        kept=lock.restore_preview(self.b,self.file,True)
        self.assertEqual(kept['skills'][0]['action'],'keep-local')
        self.assertFalse(kept['matches_lock'])
        self.assertIsNone(kept['plan_id'])
        self.assertEqual(lock.check(self.b,self.file)['skills'][0]['status'],'missing')

    def test_modified_baseline_prevents_capture_and_restore(self):
        self.create(['alpha']); self.restore()
        baseline=Path(self.b.db()['installed']['alpha']['baseline'])
        (baseline/'notes.txt').write_bytes(b'tampered baseline')
        before=fingerprint(self.b.target)
        with self.assertRaisesRegex(ValueError,'baseline'):lock.restore_preview(self.b,self.file)
        with self.assertRaisesRegex(ValueError,'baseline'):lock.create_preview(self.b,self.root/'new.lock.json')
        self.assertEqual(fingerprint(self.b.target),before)
        self.assertFalse((self.root/'new.lock.json').exists())

    def test_local_only_adopted_skill_roundtrip(self):
        shutil.copytree(self.source/'alpha',self.b.target/'alpha')
        self.b.adopt('alpha',apply=True)
        path=self.root/'local-only.json'
        lock.apply(self.b,lock.create_preview(self.b,path)['plan_id'])
        value,_,_=lock.load(path)
        self.assertIsNone(value['skills'][0]['upstream_files'])
        self.assertIsNone(lock.summary(value)[0]['customized'])
        c=Manager(self.source,self.root/'c/skills',self.root/'state-c')
        lock.apply(c,lock.restore_preview(c,path)['plan_id'])
        self.assertTrue(lock.check(c,path)['ok'])
        with self.assertRaisesRegex(ValueError,'no upstream baseline'):
            c.install('alpha')

    def test_create_requires_managed_skills_and_never_overwrites_arbitrary_file(self):
        with self.assertRaises(ValueError):lock.create_preview(self.a,self.file,['unknown'])
        self.file.parent.mkdir(); self.file.write_text('private content')
        with self.assertRaises(ValueError):lock.create_preview(self.a,self.file)
        self.assertEqual(self.file.read_text(),'private content')
        with self.assertRaises(ValueError):lock.create_preview(self.a,self.a.target/'lock.json')

    def test_stale_create_changed_install_or_existing_lock(self):
        preview=lock.create_preview(self.a,self.file)
        (self.a.target/'alpha/notes.txt').write_bytes(b'changed after preview')
        with self.assertRaisesRegex(ValueError,'stale'):lock.apply(self.a,preview['plan_id'])
        self.assertFalse(self.file.exists())
        self.create()
        (self.a.target/'alpha/notes.txt').write_bytes(b'new local version')
        preview=lock.create_preview(self.a,self.file)
        self.file.write_bytes(self.file.read_bytes()+b'\n')
        before=self.file.read_bytes()
        with self.assertRaisesRegex(ValueError,'changed after preview'):lock.apply(self.a,preview['plan_id'])
        self.assertEqual(self.file.read_bytes(),before)

    def test_restore_stale_file_target_registry_and_single_use(self):
        self.create()
        preview=lock.restore_preview(self.b,self.file)
        self.file.write_bytes(self.file.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'stale'):lock.apply(self.b,preview['plan_id'])
        self.assertFalse(self.b.target.exists())
        with self.assertRaises(FileNotFoundError):lock.apply(self.b,preview['plan_id'])
        preview=lock.restore_preview(self.b,self.file)
        shutil.copytree(self.source/'alpha',self.b.target/'alpha')
        with self.assertRaisesRegex(ValueError,'stale'):lock.apply(self.b,preview['plan_id'])
        self.assertFalse((self.b.target/'beta').exists())
        preview=lock.restore_preview(self.b,self.file,True)
        registry=self.b.db()
        registry['history']['alpha']=[]
        write_json(self.b.db_path,registry)
        with self.assertRaisesRegex(ValueError,'stale'):lock.apply(self.b,preview['plan_id'])
        self.assertFalse((self.b.target/'beta').exists())

    def test_expired_wrong_target_and_operation_lock(self):
        self.create()
        preview=lock.restore_preview(self.b,self.file)
        with self.assertRaises(FileNotFoundError):lock.apply(self.a,preview['plan_id'])
        with patch('skill_lock.time.time',return_value=10**12):
            with self.assertRaisesRegex(ValueError,'expired'):lock.apply(self.b,preview['plan_id'])
        with self.b.lock():
            with self.assertRaisesRegex(ValueError,'Operation lock'):lock.apply(self.b,preview['plan_id'])
        self.assertTrue(lock.apply(self.b,preview['plan_id'])['matches_lock'])

    def test_compensates_earlier_skill_if_second_write_fails(self):
        self.create()
        original=self.b.record
        def fail(name,*args,**kwargs):
            if name=='beta': raise OSError('injected disk failure')
            return original(name,*args,**kwargs)
        preview=lock.restore_preview(self.b,self.file)
        with patch.object(self.b,'record',side_effect=fail):
            with self.assertRaisesRegex(OSError,'disk failure'):lock.apply(self.b,preview['plan_id'])
        self.assertFalse((self.b.target/'alpha').exists())
        self.assertFalse((self.b.target/'beta').exists())
        self.assertFalse(self.b.db_path.exists())

    def test_compensation_restores_original_registry_bytes_and_files(self):
        self.b=Manager(self.source,self.b.target,self.root/'state-b')
        self.b.install('alpha'); self.b.install('beta')
        before=fingerprint(self.b.target); db_before=self.b.db_path.read_bytes()
        for name in ('alpha','beta'):
            (self.source/name/'notes.txt').write_bytes(b'new version')
            self.a.install(name)
        self.create(); preview=lock.restore_preview(self.b,self.file)
        original=self.b.record
        def fail(name,*args,**kwargs):
            if name=='beta':raise OSError('injected')
            return original(name,*args,**kwargs)
        with patch.object(self.b,'record',side_effect=fail):
            with self.assertRaises(OSError):lock.apply(self.b,preview['plan_id'])
        self.assertEqual(fingerprint(self.b.target),before)
        self.assertEqual(self.b.db_path.read_bytes(),db_before)

    def test_hash_schema_paths_collisions_and_expansion_are_rejected(self):
        self.create(['alpha']); value,_,_=lock.load(self.file)
        invalid=[]
        bad=copy.deepcopy(value); next(iter(bad['objects'].values())).update(text='tampered'); invalid.append(bad)
        bad=copy.deepcopy(value); bad['schema_version']=True; invalid.append(bad)
        bad=copy.deepcopy(value); bad['skills'][0]['files']=None; invalid.append(bad)
        for path in ('../escape','/absolute','C:/escape','x\\escape','AUX.txt','CON .txt','trailing.','a/../b','.git/config'):
            bad=copy.deepcopy(value); bad['skills'][0]['files'][path]=next(iter(value['objects'])); invalid.append(bad)
        for pair in [('Skill.md','SKILL.md'),('Folder/a','folder/b'),('a','a/b')]:
            bad=copy.deepcopy(value)
            for path in pair:bad['skills'][0]['files'][path]=next(iter(value['objects']))
            invalid.append(bad)
        for bad in invalid:
            with self.assertRaises((ValueError,TypeError)):lock.validate(bad)
        with patch('skill_lock.MAX_BYTES',100):
            with self.assertRaisesRegex(ValueError,'size limit'):lock.validate(value)
        decoded=lock.validate(value)
        with patch('skill_lock.MAX_BYTES',sum(map(len,decoded.values()))+1):
            with self.assertRaisesRegex(ValueError,'Expanded'):lock.validate(value)
        self.assertFalse(self.b.target.exists())

    def test_cli_inspect_without_config_and_create_restore_check(self):
        self.create(['alpha'])
        command=[sys.executable,'-B','-X','utf8',str(REPO/'skill_manage.py')]
        result=subprocess.run(command+['--config',str(self.root/'missing.json'),'lock','inspect','--file',str(self.file)],
                              capture_output=True,text=True,encoding='utf-8',timeout=30,cwd=self.root)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['skills'][0]['name'],'alpha')
        config=self.root/'manager.json'
        write_json(config,dict(schema_version=1,source=str(self.b_source),state=str(self.root/'state-b'),targets={'Other PC':str(self.b.target)}))
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--config',str(config),'lock','restore','--file',str(self.file)]),0)
        plan=json.loads(output.getvalue())['plan_id']
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config',str(config),'lock','apply',plan]),0)
            self.assertEqual(main(['--config',str(config),'lock','check','--file',str(self.file)]),0)


if __name__=='__main__':
    unittest.main()
