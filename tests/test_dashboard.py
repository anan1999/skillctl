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


if __name__ == '__main__':
    unittest.main()
