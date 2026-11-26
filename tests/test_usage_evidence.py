"""Coordinator regression cases for observed tool envelopes, using invented content."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from skill_usage import collect, assess, write_report

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
STAMP = (NOW - timedelta(days=1)).isoformat()
BODY = '---\nname: demo\ndescription: Synthetic\n---\nInstructions.'


def record(kind, **payload):
    return dict(timestamp=STAMP, type='response_item', payload=dict(type=kind, **payload))


def read_call(identity, command):
    return record('function_call', call_id=identity, name='exec_command',
                  arguments=json.dumps(dict(cmd=command)))


def output(identity, code=0, body=BODY):
    return record('function_call_output', call_id=identity,
                  output=json.dumps(dict(exit_code=code, output=body)))


class EvidenceTests(unittest.TestCase):
    def collect_rows(self, rows, fork=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sessions').mkdir()
            meta = dict(type='session_meta', timestamp=(NOW-timedelta(days=60)).isoformat(),
                        payload=dict(id='original', timestamp=(NOW-timedelta(days=60)).isoformat()))
            (root/'sessions/a.jsonl').write_text('\n'.join(map(json.dumps, [meta]+rows)), encoding='utf-8')
            if fork:
                meta['payload'] = dict(id='fork', timestamp=(NOW-timedelta(hours=1)).isoformat())
                (root/'sessions/b.jsonl').write_text('\n'.join(map(json.dumps, [meta]+rows)), encoding='utf-8')
            return collect(root, as_of=NOW)

    def test_realistic_nested_envelope_and_fork_replay(self):
        code = 'text(await tools.exec_command({cmd:"[IO.File]::ReadAllText(\'C:/skills/demo/SKILL.md\')"}));'
        rows = [record('custom_tool_call', call_id='nested', name='exec', input=code),
                record('custom_tool_call_output', call_id='nested', output=[
                    dict(type='input_text', text='Script completed\nOutput:'),
                    dict(type='input_text', text=json.dumps(dict(exit_code=0, output=BODY)))])]
        result = assess(self.collect_rows(rows, fork=True), 'demo', 7, now=NOW)
        self.assertEqual((result['reads'], result['conversations']), (1, 1))

    def test_unrelated_read_and_error_chunk_are_not_evidence(self):
        rows = []
        for index, command in enumerate([
            'cat OTHER.txt; echo C:/skills/demo/SKILL.md',
            'git diff C:/skills/demo/SKILL.md',
            'echo "ReadAllText(\'C:/skills/demo/SKILL.md\')"',
        ]):
            rows += [read_call(str(index), command), output(str(index))]
        rows += [read_call('failed', "[IO.File]::ReadAllText('C:/skills/demo/SKILL.md')"),
                 record('function_call_output', call_id='failed', output=[
                     dict(type='input_text', text=json.dumps(dict(exit_code=0, output='unrelated'))),
                     dict(type='input_text', text=json.dumps(dict(exit_code=1, output=BODY)))])]
        self.assertEqual(assess(self.collect_rows(rows), 'demo', 30, now=NOW)['reads'], 0)

    def test_output_name_and_complete_frontmatter_required(self):
        rows = []
        for identity, body in [('wrong', BODY.replace('name: demo', 'name: other')),
                               ('mention', 'This document mentions name: demo'),
                               ('partial', '---\nname: demo\ndescription: truncated')]:
            rows += [read_call(identity, 'cat C:/skills/demo/SKILL.md'), output(identity, body=body)]
        self.assertEqual(assess(self.collect_rows(rows), 'demo', 7, now=NOW)['reads'], 0)

    def test_multiple_literal_reads_in_one_shell_call(self):
        command = "[IO.File]::ReadAllText('C:/skills/demo/SKILL.md'); [IO.File]::ReadAllText('C:/skills/other/SKILL.md')"
        rows = [read_call('batch', command), output('batch', body=BODY+'\n'+BODY.replace('name: demo', 'name: other'))]
        report = self.collect_rows(rows)
        for name in ('demo', 'other'):
            self.assertEqual(assess(report, name, 7, now=NOW)['reads'], 1)

    def test_failed_status_never_counts_as_native_read(self):
        rows = [record('function_call', name='read_file', call_id='native', arguments=json.dumps({'path':'C:/skills/demo/SKILL.md'})),
                record('function_call_output', call_id='native', output={'status':'error','content':BODY})]
        self.assertEqual(assess(self.collect_rows(rows), 'demo', 7, now=NOW)['reads'], 0)

    def test_refresh_is_atomic_and_refuses_nonreport(self):
        report = self.collect_rows([read_call('one','cat C:/skills/demo/SKILL.md'),output('one')])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'usage.json'
            write_report(report,path)
            write_report(report,path)
            self.assertEqual(json.loads(path.read_text())['skills']['demo']['7']['reads'],1)
            path.write_text('preserve user content')
            with self.assertRaises((ValueError,OSError)):
                write_report(report,path)
            self.assertEqual(path.read_text(),'preserve user content')

    def test_stale_cutoff_cannot_be_hidden_by_new_generation_date(self):
        report = self.collect_rows([read_call('one','cat C:/skills/demo/SKILL.md'),output('one')])
        report['generated_at'] = (NOW + timedelta(days=10)).isoformat()
        self.assertTrue(assess(report,'demo',7,now=NOW+timedelta(days=10))['stale'])


if __name__ == '__main__':
    unittest.main()
