import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from skillctl import Manager, fingerprint
from skillctl_content import merge_trees, text_diff


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.skill = self.source / 'example-skill'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: example-skill\ndescription: Test skill\n---\nTest\n', encoding='utf-8')
        (self.skill / 'notes.txt').write_text('one\ntwo\nthree\nfour\nfive\nsix\nseven\n', encoding='utf-8')
        self.target = self.root / 'target'
        self.local = self.target / 'example-skill'
        self.state = self.root / 'state'
        self.m = Manager(self.source, self.target, self.state)

    def test_discover_without_catalog_and_scan_does_not_write(self):
        shutil.copytree(self.skill, self.local)
        before = fingerprint(self.root)
        self.assertEqual('unmanaged-identical', self.m.scan()[0]['status'])
        self.m.adopt('example-skill')
        self.assertEqual(before, fingerprint(self.root))
        self.assertFalse(self.state.exists())

    def test_adopt_preserves_custom_bytes_and_rollback_unmanages(self):
        shutil.copytree(self.skill, self.local)
        (self.local / 'notes.txt').write_text('my edits\n', encoding='utf-8')
        before = fingerprint(self.local)
        self.m.adopt('example-skill', apply=True)
        self.assertEqual(before, fingerprint(self.local))
        self.assertEqual('customized', self.m.listing()[0]['status'])
        with self.assertRaisesRegex(ValueError, 'customizations'):
            self.m.install('example-skill')
        self.m.rollback('example-skill')
        self.assertEqual(before, fingerprint(self.local))
        self.assertNotIn('example-skill', self.m.db()['installed'])

    def test_adopt_unknown_skill_and_reopen_registry(self):
        empty = self.root / 'empty-source'
        empty.mkdir()
        shutil.copytree(self.skill, self.local)
        m = Manager(empty, self.target, self.state)
        self.assertEqual('unmanaged-local', m.scan()[0]['status'])
        m.adopt('example-skill', apply=True)
        m = Manager(empty, self.target, self.state)
        self.assertEqual('local-only', m.listing()[0]['status'])
        self.assertTrue(m.doctor([])['ok'])
        with self.assertRaisesRegex(ValueError, 'No upstream'):
            m.install('example-skill')
        bound = Manager(self.source, self.target, self.state)
        with self.assertRaisesRegex(ValueError, 'no upstream baseline'):
            bound.install('example-skill')
        bound.reconcile('example-skill', 'keep', apply=True)
        self.assertEqual('current', bound.listing()[0]['status'])

    def test_merge_nonoverlapping_text_and_rollback(self):
        self.m.install('example-skill')
        (self.local / 'notes.txt').write_text('LOCAL\ntwo\nthree\nfour\nfive\nsix\nseven\n', encoding='utf-8')
        (self.skill / 'notes.txt').write_text('one\ntwo\nthree\nfour\nfive\nsix\nUPSTREAM\n', encoding='utf-8')
        before = fingerprint(self.local)
        preview = self.m.reconcile('example-skill', 'merge')
        self.assertTrue(preview['ok'])
        self.assertEqual(before, fingerprint(self.local))
        self.m.reconcile('example-skill', 'merge', apply=True)
        content = (self.local / 'notes.txt').read_text()
        self.assertIn('LOCAL', content)
        self.assertIn('UPSTREAM', content)
        self.m.rollback('example-skill')
        self.assertEqual(before, fingerprint(self.local))

    def test_conflict_leaves_target_and_receipt_unchanged(self):
        self.m.install('example-skill')
        (self.local / 'notes.txt').write_text('local\n')
        (self.skill / 'notes.txt').write_text('upstream\n')
        before, db = fingerprint(self.local), self.m.db()
        result = self.m.reconcile('example-skill', 'merge', apply=True)
        self.assertFalse(result['ok'])
        self.assertIn('<<<<<<< LOCAL', result['conflicts'][0]['preview'])
        self.assertEqual(before, fingerprint(self.local))
        self.assertEqual(db, self.m.db())
        diff = self.m.diff('example-skill', True)
        self.assertIn('overwrite', diff['blocked_reason'])
        self.assertTrue(diff['baseline_available'])

    def test_keep_still_blocks_install_and_future_merge_keeps_customization(self):
        self.m.install('example-skill')
        (self.local / 'custom.txt').write_text('keep me')
        (self.skill / 'notes.txt').write_text('upstream v2\n')
        before = fingerprint(self.local)
        self.m.reconcile('example-skill', 'keep', apply=True)
        self.assertEqual(before, fingerprint(self.local))
        with self.assertRaisesRegex(ValueError, 'customizations'):
            self.m.install('example-skill')
        (self.skill / 'new.txt').write_text('v3 addition')
        self.m.reconcile('example-skill', 'merge', apply=True)
        self.assertEqual('keep me', (self.local / 'custom.txt').read_text())
        self.assertEqual('v3 addition', (self.local / 'new.txt').read_text())
        self.assertNotEqual('upstream v2\n', (self.local / 'notes.txt').read_text())

    def test_binary_delete_and_add_conflicts(self):
        cases = [({'a': b'\0a'}, {'a': b'\0local'}, {'a': b'\0upstream'}),
                 ({'a': b'old'}, {}, {'a': b'changed'}),
                 ({}, {'a': b'local'}, {'a': b'upstream'}),
                 ({}, {'a': b'file'}, {'a/b': b'nested'})]
        for base, local, upstream in cases:
            self.assertTrue(merge_trees(base, local, upstream)[1])
        merged, conflicts = merge_trees({'a': b'old', 'b': b'\0old'},
                                       {'a': b'old', 'b': b'\0old'}, {'b': b'\0new'})
        self.assertFalse(conflicts)
        self.assertEqual({'b': b'\0new'}, merged)

    def test_legacy_baseline_unavailable_blocks_merge_but_keep_works(self):
        self.m.install('example-skill')
        db = self.m.db()
        db['installed']['example-skill'].pop('baseline')
        db['installed']['example-skill'].pop('upstream_files')
        self.m.db_path.write_text(json.dumps(db))
        (self.skill / 'new').write_text('new')
        with self.assertRaisesRegex(ValueError, 'No verified common baseline'):
            self.m.reconcile('example-skill', 'merge')
        self.m.reconcile('example-skill', 'keep', apply=True)

    def test_tampered_baseline_blocks_merge(self):
        self.m.install('example-skill')
        base = Path(self.m.db()['installed']['example-skill']['baseline'])
        (base / 'tampered').write_text('bad')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.m.reconcile('example-skill', 'merge')

    def test_manual_resolution_and_markers(self):
        self.m.install('example-skill')
        resolved = self.root / 'resolved'
        shutil.copytree(self.local, resolved)
        (resolved / 'notes.txt').write_text('<<<<<<< LOCAL\n')
        with self.assertRaisesRegex(ValueError, 'markers'):
            self.m.reconcile('example-skill', 'merge', resolved=resolved)
        (resolved / 'notes.txt').write_text('reviewed result\n')
        self.m.reconcile('example-skill', 'merge', apply=True, resolved=resolved)
        self.assertEqual('reviewed result\n', (self.local / 'notes.txt').read_text())

    def test_registry_failure_compensates_merge(self):
        self.m.install('example-skill')
        (self.skill / 'new.txt').write_text('new')
        before = fingerprint(self.local)
        import skillctl
        real = skillctl.write_json
        def fail(path, data):
            if path == self.m.db_path:
                raise OSError('disk full')
            return real(path, data)
        with patch('skillctl.write_json', side_effect=fail):
            with self.assertRaises(OSError):
                self.m.reconcile('example-skill', 'merge', apply=True)
        self.assertEqual(before, fingerprint(self.local))

    def test_nested_invalid_and_duplicate_sources(self):
        nested = self.target / 'group' / 'example-skill'
        shutil.copytree(self.skill, nested)
        self.assertFalse(self.m.scan()[0]['adoptable'])
        with self.assertRaises(ValueError):
            self.m.adopt('../source', apply=True)
        shutil.copytree(self.skill, self.source / 'group/example-skill')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            Manager(self.source, self.target, self.state)

    def test_text_diff_explains_byte_only_changes(self):
        for before, after in [(b'hi\r\n', b'hi\n'), (None, b''), (b'hi', b'\xef\xbb\xbfhi')]:
            self.assertIn('byte-level', text_diff(before, after, 'a', 'b')['diff'])
        self.assertNotIn('\r', text_diff(b'old\r\n', b'new\r\n', 'a', 'b')['diff'])

    def test_preview_blocks_invalid_merged_link(self):
        (self.skill / 'ref.txt').write_text('reference')
        self.m.install('example-skill')
        (self.local / 'SKILL.md').write_text((self.local / 'SKILL.md').read_text() + '[ref](ref.txt)\n')
        (self.skill / 'ref.txt').unlink()
        before = fingerprint(self.local)
        result = self.m.reconcile('example-skill', 'merge')
        self.assertFalse(result['ok'])
        self.assertIn('Broken local link', result['conflicts'][0]['reason'])
        self.assertEqual(before, fingerprint(self.local))


if __name__ == '__main__':
    unittest.main()
