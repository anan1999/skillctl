from pathlib import Path
import tempfile
import unittest

from workflow_acceptance import exercise


class ComplexAcceptanceTests(unittest.TestCase):
    def test_multi_target_merge_conflict_rollback_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            result=exercise(Path(temp)/'acceptance')
            self.assertTrue(result['ok'])
            self.assertEqual(len(result['checks']),8)
            self.assertTrue(all(item['passed'] for item in result['checks']))


if __name__=='__main__': unittest.main()
