import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from quality_demo import create_fixture
from skillctl import fingerprint


class UsageDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_fixture(self.root)
        self.server = __import__('skill_dashboard').make_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def get(self, query='', status=200, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request('GET', '/api/usage?' + query,
                               headers=headers if headers is not None else {'X-Skillctl-Token': self.app.secret})
            result = connection.getresponse()
            body = json.loads(result.read())
            self.assertEqual(result.status, status, body)
            return body
        finally:
            connection.close()

    def test_windows_quality_scope_and_readonly(self):
        before = fingerprint(self.root)
        query = 'target=0&name=failure-triage&days='
        week = self.get(query+'7'); month = self.get(query+'30')
        self.assertEqual((week['reads'], week['conversations']), (3, 2))
        self.assertEqual((month['reads'], month['conversations']), (8, 4))
        self.assertEqual(self.app.quality('0', 'failure-triage')['status'], 'passed')
        self.assertEqual(self.get('target=0&name=untested-demo')['reads'], 0)
        pi = self.get('target=1&name=failure-triage')
        self.assertEqual(pi['status'], 'unsupported')
        self.assertIsNone(pi['reads'])
        self.assertEqual(before, fingerprint(self.root))

    def test_unavailable_corrupt_oversize_and_fresh_file(self):
        query = 'target=0&name=failure-triage'
        report = self.app.usage_report
        original = report.read_bytes()
        self.app.usage_report = None
        self.assertEqual(self.get(query)['status'], 'unavailable')
        self.app.usage_report = report
        for content in (b'{bad', b'{}'*1100000, b'{"schema_version":1,"schema_version":2}', b'null', b'[]'):
            report.write_bytes(content)
            result = self.get(query)
            self.assertEqual(result['status'], 'error')
            self.assertIsNone(result['reads'])
        report.write_bytes(original)
        self.assertEqual(self.get(query)['reads'], 3)
        report.unlink()
        self.assertEqual(self.get(query)['status'], 'error')

    def test_validation_and_auth(self):
        for query in ('target=unknown&name=failure-triage', 'target=0&name=../secret',
                      'target=0&name=failure-triage&days=8', 'target=0&name=failure-triage&days=no'):
            self.get(query, status=400)
        for headers in ({}, {'X-Skillctl-Token':self.app.secret, 'Origin':'https://example.com'}):
            self.get('target=0&name=failure-triage', status=403, headers=headers)


if __name__ == '__main__':
    unittest.main()
