import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from skill_dashboard import Dashboard, make_server
from skillctl import fingerprint


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.skill = self.source / 'example-skill'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: example-skill\ndescription: 相機時間檢查 <script>bad()</script>\n---\nRead notes.\n', encoding='utf-8')
        (self.skill / 'notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nlast\n')
        self.target = self.root / 'target'
        self.local = self.target / 'example-skill'
        shutil.copytree(self.skill, self.local)
        self.app = Dashboard(self.source, [('Pi test', self.target)], self.root / 'state')
        self.server = make_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, code=200, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            req_headers = {'X-Skillctl-Token': self.app.secret, 'Content-Type': 'application/json'}
            if headers:
                req_headers.update(headers)
            conn.request('GET' if body is None else 'POST', path,
                         body=None if body is None else json.dumps(body), headers=req_headers)
            response = conn.getresponse()
            data = response.read()
            self.assertEqual(code, response.status, data)
            return json.loads(data) if 'application/json' in response.getheader('Content-Type', '') else data.decode()
        finally:
            conn.close()

    def preview(self, action):
        return self.request('/api/preview', {'target': '0', 'name': 'example-skill', 'action': action})

    def adopt(self):
        plan = self.preview('adopt')
        return self.request('/api/apply', {'plan_id': plan['plan_id']})

    def test_inventory_detail_preview_are_read_only_and_assets_load(self):
        before = fingerprint(self.root)
        rows = self.request('/api/inventory')['targets'][0]['skills']
        self.assertEqual('unmanaged-identical', rows[0]['status'])
        self.assertIn('相機', rows[0]['description'])
        detail = self.request('/api/detail?target=0&name=example-skill')
        self.assertEqual({}, {k:v for k,v in detail.items() if k == 'error'})
        self.assertTrue(self.preview('adopt')['plan_id'])
        self.assertEqual(before, fingerprint(self.root))
        self.assertIn(self.app.secret, self.request('/'))
        self.assertIn('textContent', self.request('/dashboard.js'))
        self.assertIn('@media', self.request('/dashboard.css'))
        self.request('/../skillctl.py', code=404)

    def test_config_is_immediate_and_scan_is_limited_to_requested_target(self):
        self.app.targets['1'] = {'label': 'Other target', 'path': str(self.root / 'other')}
        with patch.object(self.app, 'manager', side_effect=AssertionError('config must not scan')):
            config = self.request('/api/config')
        self.assertEqual(['0', '1'], [t['id'] for t in config['targets']])
        with patch.object(self.app, 'manager', wraps=self.app.manager) as manager:
            result = self.request('/api/inventory?target=0')
        manager.assert_called_once_with('0')
        self.assertEqual(['0'], [t['id'] for t in result['targets']])
        self.request('/api/inventory?target=unknown', code=400)
        self.request('/api/config', code=403, headers={'X-Skillctl-Token': ''})

    def test_counts_distinguish_installed_managed_missing_and_invalid(self):
        other = self.source / 'not-installed'
        other.mkdir()
        (other / 'SKILL.md').write_text('---\nname: not-installed\ndescription: Missing\n---\n')
        broken = self.target / 'broken'
        broken.mkdir()
        (broken / 'SKILL.md').write_text('Missing frontmatter')
        counts = self.request('/api/inventory?target=0')['targets'][0]['counts']
        self.assertEqual({'installed': 2, 'managed': 0, 'unmanaged': 2, 'missing': 1, 'attention': 1}, counts)
        self.adopt()
        counts = self.request('/api/inventory?target=0')['targets'][0]['counts']
        self.assertEqual((2, 1, 1), (counts['installed'], counts['managed'], counts['unmanaged']))
        shutil.rmtree(self.local)
        counts = self.request('/api/inventory?target=0')['targets'][0]['counts']
        self.assertEqual((1, 0, 2), (counts['installed'], counts['managed'], counts['missing']))

    def test_adopt_then_merge_keeps_local_and_adds_upstream(self):
        self.adopt()
        (self.local / 'notes.txt').write_text('LOCAL\ntwo\nthree\nfour\nfive\nsix\nlast\n')
        (self.skill / 'notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nUPSTREAM\n')
        before = fingerprint(self.local)
        plan = self.preview('merge')
        self.assertEqual(before, fingerprint(self.local))
        self.assertIn('UPSTREAM', plan['result']['changes'][0]['diff'])
        result = self.request('/api/apply', {'plan_id': plan['plan_id']})
        self.assertEqual('merged', result['result']['action'])
        self.assertIn('LOCAL', (self.local / 'notes.txt').read_text())
        self.assertIn('UPSTREAM', (self.local / 'notes.txt').read_text())
        self.app.manager('0').rollback('example-skill')
        self.assertEqual(before, fingerprint(self.local))

    def test_overlap_blocks_apply_and_keep_preserves_bytes(self):
        self.adopt()
        (self.local / 'notes.txt').write_text('local\n')
        (self.skill / 'notes.txt').write_text('upstream\n')
        before = fingerprint(self.local)
        plan = self.preview('merge')
        self.assertIsNone(plan['plan_id'])
        self.assertFalse(plan['result']['ok'])
        self.assertIn('<<<<<<< LOCAL', plan['result']['conflicts'][0]['preview'])
        plan = self.preview('keep')
        self.request('/api/apply', {'plan_id': plan['plan_id']})
        self.assertEqual(before, fingerprint(self.local))
        self.assertEqual('customized', self.app.manager('0').listing()[0]['status'])

    def test_stale_local_upstream_or_receipt_rejects_apply(self):
        for changed in ('local', 'upstream', 'receipt'):
            with self.subTest(changed=changed):
                plan = self.preview('adopt')
                if changed == 'local':
                    (self.local / 'local').write_text('new edit')
                elif changed == 'upstream':
                    (self.skill / 'upstream').write_text('new version')
                else:
                    self.app.manager('0').adopt('example-skill', apply=True)
                before = fingerprint(self.local)
                result = self.request('/api/apply', {'plan_id':plan['plan_id']}, code=409)
                self.assertIn('stale', result['error'])
                self.assertEqual(before, fingerprint(self.local))

    def test_reconcile_stale_check_happens_inside_operation_lock(self):
        self.adopt()
        plan = self.preview('keep')
        manager = self.app.manager('0')
        (self.local / 'fresh').write_text('do not overwrite')
        before = manager.db()
        with self.assertRaisesRegex(ValueError, 'stale'):
            manager.reconcile('example-skill', 'keep', apply=True, expected=self.app.plans[plan['plan_id']]['stamp'])
        self.assertEqual(before, manager.db())

    def test_preview_is_single_use_expiring_and_bound_to_action(self):
        plan = self.preview('adopt')
        self.request('/api/apply', {'plan_id': plan['plan_id'], 'action':'keep'}, code=409)
        self.request('/api/apply', {'plan_id': plan['plan_id']})
        self.request('/api/apply', {'plan_id': plan['plan_id']}, code=409)
        plan = self.preview('keep')
        self.app.plans[plan['plan_id']]['expires'] = 0
        self.request('/api/apply', {'plan_id': plan['plan_id']}, code=409)

    def test_cross_origin_host_and_missing_capability_rejected(self):
        for headers in ({'Origin':'https://example.org'}, {'Origin':'null'},
                        {'Host':'example.org'}, {'X-Skillctl-Token':''}):
            with self.subTest(headers=headers):
                self.request('/api/inventory', code=403, headers=headers)
                self.request('/api/preview', {'target':'0','name':'example-skill','action':'adopt'}, code=403, headers=headers)
        self.request('/api/preview', {'target':'../../elsewhere','name':'example-skill','action':'adopt'}, code=409)
        self.request('/api/preview', {'target':'0','name':'../escape','action':'adopt'}, code=409)
        self.assertFalse((self.root / 'state').exists())

    def test_locked_apply_preserves_target_and_requires_new_preview(self):
        plan = self.preview('adopt')
        manager = self.app.manager('0')
        before = fingerprint(self.local)
        with manager.lock():
            self.request('/api/apply', {'plan_id': plan['plan_id']}, code=409)
        self.assertEqual(before, fingerprint(self.local))
        self.assertNotIn('example-skill', manager.db()['installed'])

    def test_unknown_local_skill_can_be_adopted_without_upstream(self):
        folder = self.target / 'local-only'
        folder.mkdir()
        (folder / 'SKILL.md').write_text('---\nname: local-only\ndescription: Local skill\n---\nKeep me.\n')
        before = fingerprint(folder)
        detail = self.request('/api/detail?target=0&name=local-only')
        self.assertIsNone(detail['comparison'])
        plan = self.request('/api/preview', {'target':'0', 'name':'local-only', 'action':'adopt'})
        self.request('/api/apply', {'plan_id':plan['plan_id']})
        row = next(row for row in self.request('/api/inventory')['targets'][0]['skills'] if row['name'] == 'local-only')
        self.assertTrue(row['managed'])
        self.assertFalse(row['has_source'])
        self.assertEqual('local-only', row['status'])
        self.assertEqual(before, fingerprint(folder))

    def test_install_preview_targets_selected_harness_and_first_rollback_removes_install(self):
        second = self.root / 'project/.pi/skills'
        self.app.targets['1'] = {'label': 'Pi project', 'path': str(second)}
        before = fingerprint(self.root)
        plan = self.request('/api/preview', {'target': '1', 'name': 'example-skill', 'action': 'install'})
        self.assertEqual('1', plan['target_id'])
        self.assertEqual('would-install', plan['result']['action'])
        self.assertEqual(before, fingerprint(self.root))
        self.assertIn('SKILL.md', [c['file'] for c in plan['result']['changes']])
        self.request('/api/apply', {'plan_id': plan['plan_id']})
        self.assertEqual(fingerprint(self.skill), fingerprint(second / 'example-skill'))
        self.assertNotIn('example-skill', self.app.manager('0').db()['installed'])
        detail = self.request('/api/detail?target=1&name=example-skill')
        self.assertEqual('/skill:example-skill', detail['invocation']['command'])
        self.assertTrue(detail['invocation']['installed'])
        self.assertFalse(detail['invocation']['runtime_verified'])
        self.assertIn(str((second / 'example-skill/SKILL.md').resolve()), detail['invocation']['prompt'])
        before = fingerprint(self.root)
        rollback = self.request('/api/preview', {'target': '1', 'name': 'example-skill', 'action': 'rollback'})
        self.assertTrue(rollback['result']['removes_directory'])
        self.assertEqual(before, fingerprint(self.root))
        self.request('/api/apply', {'plan_id': rollback['plan_id']})
        self.assertFalse((second / 'example-skill').exists())

    def test_update_rollback_restores_bytes_and_rollback_does_not_depend_on_upstream(self):
        self.adopt()
        original = fingerprint(self.local)
        (self.skill / 'notes.txt').write_text('new upstream\n')
        before = fingerprint(self.root)
        plan = self.preview('install')
        self.assertEqual('would-update', plan['result']['action'])
        self.assertEqual(before, fingerprint(self.root))
        self.request('/api/apply', {'plan_id': plan['plan_id']})
        self.assertEqual(fingerprint(self.skill), fingerprint(self.local))
        (self.skill / 'SKILL.md').write_text('invalid upstream')
        rollback = self.preview('rollback')
        self.assertFalse(rollback['result']['removes_directory'])
        self.assertTrue(rollback['result']['restores_managed'])
        self.request('/api/apply', {'plan_id': rollback['plan_id']})
        self.assertEqual(original, fingerprint(self.local))
        undo_adoption = self.preview('rollback')
        self.assertFalse(undo_adoption['result']['restores_managed'])
        self.request('/api/apply', {'plan_id': undo_adoption['plan_id']})
        self.assertEqual(original, fingerprint(self.local))
        self.assertNotIn('example-skill', self.app.manager('0').db()['installed'])

    def test_install_and_rollback_stale_previews_never_overwrite_changes(self):
        self.adopt()
        (self.skill / 'notes.txt').write_text('new upstream\n')
        for action in ('install', 'rollback'):
            with self.subTest(action=action):
                plan = self.preview(action)
                (self.local / 'edit.txt').write_text('keep this change')
                before = fingerprint(self.local)
                response = self.request('/api/apply', {'plan_id': plan['plan_id']}, code=409)
                self.assertIn('stale', response['error'])
                self.assertEqual(before, fingerprint(self.local))
                (self.local / 'edit.txt').unlink()
        plan = self.preview('install')
        (self.skill / 'notes.txt').write_text('changed after preview')
        self.request('/api/apply', {'plan_id': plan['plan_id']}, code=409)

    def test_install_blocks_unmanaged_customized_and_drift_rollback_snapshots_are_checked(self):
        self.request('/api/preview', {'target':'0','name':'example-skill','action':'install'}, code=409)
        self.adopt()
        (self.local / 'edit.txt').write_text('custom local')
        self.request('/api/preview', {'target':'0','name':'example-skill','action':'install'}, code=409)
        self.request('/api/preview', {'target':'0','name':'example-skill','action':'rollback'}, code=409)
        kept = self.preview('keep')
        self.request('/api/apply', {'plan_id':kept['plan_id']})
        self.request('/api/preview', {'target':'0','name':'example-skill','action':'install'}, code=409)
        rollback = self.preview('rollback')
        backup = Path(self.app.manager('0').db()['history']['example-skill'][-1]['backup'])
        (backup / 'notes.txt').write_text('tampered')
        self.request('/api/apply', {'plan_id':rollback['plan_id']}, code=409)
        self.request('/api/preview', {'target':'0','name':'example-skill','action':'rollback'}, code=409)

    def test_new_actions_verify_preview_inside_lock_and_noop_has_no_apply_token(self):
        self.adopt()
        self.assertIsNone(self.preview('install')['plan_id'])
        (self.skill / 'notes.txt').write_text('update')
        manager = self.app.manager('0')
        for action in ('install','rollback'):
            stamp = manager.preview_stamp('example-skill', action)
            with manager.lock():
                with self.assertRaisesRegex(ValueError, 'Operation lock'):
                    getattr(manager, action)('example-skill', expected=stamp)
            (self.local / 'edit').write_text('new')
            with self.assertRaisesRegex(ValueError, 'stale'):
                getattr(manager, action)('example-skill', expected=stamp)
            (self.local / 'edit').unlink()

    def test_doctor_reports_invalid_and_different_copies_without_mutating(self):
        other = self.root / 'other/.agents/skills'
        shutil.copytree(self.local, other / 'example-skill')
        (other / 'example-skill/notes.txt').write_text('different')
        self.app.targets['1'] = {'label':'Codex', 'path': str(other)}
        broken = self.target / 'broken'
        broken.mkdir()
        (broken / 'SKILL.md').write_text('missing frontmatter')
        before = fingerprint(self.root)
        report = self.request('/api/doctor?target=0')
        self.assertEqual(before, fingerprint(self.root))
        self.assertFalse(report['runtime_verified'])
        example = next(row for row in report['skills'] if row['name']=='example-skill')
        self.assertEqual(2,len(example['copies']))
        self.assertFalse(example['duplicate_content_equal'])
        self.assertEqual('invalid', next(row for row in report['skills'] if row['name']=='broken')['status'])
        invocation = self.request('/api/detail?target=1&name=example-skill')['invocation']
        self.assertEqual('$example-skill', invocation['command'])
        self.request('/api/doctor?target=outside',code=400)
        self.request('/api/doctor?target=0&name=../escape',code=400)
        self.request('/api/doctor?target=0',code=403,headers={'X-Skillctl-Token':''})

    def test_missing_source_descriptions_are_searchable_and_invocation_is_not_ready(self):
        shutil.rmtree(self.local)
        rows = self.request('/api/inventory?target=0')['targets'][0]['skills']
        self.assertIn('相機',rows[0]['description'])
        self.assertFalse(self.request('/api/detail?target=0&name=example-skill')['invocation']['installed'])


if __name__ == '__main__':
    unittest.main()
