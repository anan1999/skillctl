import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import skill_usage as usage

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


def event(day, call, code=0):
    stamp = (NOW-timedelta(days=day)).isoformat()
    return [
        dict(timestamp=stamp, type='response_item', payload=dict(type='function_call', name='exec_command',
             call_id=call, arguments=json.dumps({'cmd':"cat /skills/demo/SKILL.md"}))),
        dict(timestamp=stamp, type='response_item', payload=dict(type='function_call_output', call_id=call,
             output=json.dumps(dict(exit_code=code,output='---\nname: demo\ndescription: PRIVATE\n---\nSecret task'))))]


class UsageTests(unittest.TestCase):
    def report(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'sessions').mkdir()
            path = root/'sessions/log.jsonl'
            original = '\n'.join(map(json.dumps,rows))
            path.write_text(original,encoding='utf-8')
            result = usage.collect(root,as_of=NOW)
            self.assertEqual(path.read_text(encoding='utf-8'),original)
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertNotIn('Secret task',json.dumps(result))
            self.assertNotIn(str(root),json.dumps(result))
            return result

    def test_boundaries_duplicate_failed_and_privacy(self):
        rows = event(1,'recent')+event(20,'older')+event(31,'expired')+event(2,'failed',1)
        rows += event(1,'recent')+event(0,'at-end')+event(7,'at-seven')+event(30,'at-thirty')
        report = self.report(rows)
        self.assertEqual(usage.assess(report,'demo',7,now=NOW)['reads'],2)
        self.assertEqual(usage.assess(report,'demo',30,now=NOW)['reads'],4)
        self.assertEqual(report['coverage']['duplicate_events'],1)
        self.assertEqual(report['coverage']['unconfirmed_reads'],1)
        self.assertEqual(usage.assess(report,'absent',7,now=NOW)['reads'],0)

    def test_invalid_reports_and_unavailable_are_not_zero(self):
        report = self.report(event(1,'one'))
        for mutate in [
            lambda x:x['skills']['demo']['7'].update(reads=True),
            lambda x:x['skills']['demo']['7'].update(conversations=2),
            lambda x:x['skills']['demo']['30'].update(reads=0),
            lambda x:x.update(schema_version=True),
            lambda x:x.update(source='other'),
            lambda x:x['coverage'].update(malformed_lines=-1),
            lambda x:x.update(window_end=(NOW+timedelta(days=3)).isoformat()),
        ]:
            bad=copy.deepcopy(report);mutate(bad)
            result=usage.assess(bad,'demo',7,now=NOW)
            self.assertEqual(result['status'],'error')
            self.assertIsNone(result['reads'])
        self.assertEqual(usage.assess(None,'demo',7)['status'],'unavailable')
        self.assertEqual(usage.assess(report,'demo',7,harness='pi')['status'],'unsupported')
        for days in (True,7.0,8,'7'):
            with self.assertRaises(ValueError):usage.assess(report,'demo',days)

    def test_malformed_oversize_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir()
            (root/'sessions/log.jsonl').write_text('{bad\n'+'x'*300+'\n'+
                '{"type":"response_item","type":"other"}\n',encoding='utf-8')
            with patch.object(usage,'MAX_LINE_BYTES',256):
                report=usage.collect(root,as_of=NOW)
            self.assertEqual(report['coverage']['malformed_lines'],2)
            self.assertEqual(report['coverage']['oversized_lines'],1)
            self.assertTrue(usage.assess(report,'demo',7,now=NOW)['partial'])

    def test_wrong_directory_is_not_empty_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):usage.collect(Path(folder))

    def test_snapshot_line_reader_does_not_consume_appended_bytes(self):
        import io
        self.assertEqual(list(usage.iter_lines(io.BytesIO(b'first\nsecond\n'),6)),[b'first\n'])

    def test_linked_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir()
            file=root/'sessions/log.jsonl';file.write_text('\n'.join(map(json.dumps,event(1,'one'))))
            original=usage.is_link
            with patch.object(usage,'is_link',side_effect=lambda path: Path(path)==file or original(path)):
                report=usage.collect(root,as_of=NOW)
            self.assertEqual(report['coverage']['files_scanned'],0)


if __name__ == '__main__':
    unittest.main()
