from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from demo import run_demo, report, LOCAL, UPSTREAM


class DemoTests(unittest.TestCase):
    def test_real_file_protection_merge_and_escaped_report(self):
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket', side_effect=AssertionError('No network')):
            result = run_demo(Path(directory))
        self.assertEqual(len(result['checks']), 5)
        self.assertIn(LOCAL, result['merged'])
        self.assertIn(UPSTREAM, result['merged'])
        self.assertEqual(len(set(result['hashes'].values())),3)
        self.assertIn('SKILL.md', result['patch'])
        result['patch'] = '<img src=x onerror=alert(1)>'
        html = report(result)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
        self.assertIn('id="step-2"', html)
        self.assertNotIn('https://', html)


if __name__ == '__main__':
    unittest.main()
