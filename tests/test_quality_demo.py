import tempfile
import unittest
from pathlib import Path

from quality_demo import create_fixture


class QualityDemoTests(unittest.TestCase):
    def test_four_states_without_private_skills_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_fixture(folder)
            for name, expected in [('failure-triage', 'passed'), ('sysclaw-board-ipcam-time', 'failed'),
                                   ('agent-control', 'stale'), ('untested-demo', 'untested')]:
                result = app.quality('0', name)
                self.assertEqual(result['status'], expected)
                if expected != 'untested':
                    self.assertEqual(result['model'], 'DEMO-NO-MODEL')
                    self.assertEqual(result['request_binding'], 'not-recorded')

    def test_refuses_existing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / 'preserve.txt'; existing.write_text('keep', encoding='utf-8')
            with self.assertRaises(ValueError): create_fixture(folder)
            self.assertEqual(existing.read_text(encoding='utf-8'), 'keep')
