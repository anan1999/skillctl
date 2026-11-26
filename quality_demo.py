"""Show quality states using disposable synthetic skills; no model or device calls."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import webbrowser

from skill_dashboard import Dashboard, make_server
from skill_quality import prepare, grade
from skillctl import write_json


def create_fixture(root):
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError('Quality demo requires an empty fixture directory')
    root.mkdir(parents=True, exist_ok=True)
    skills_root = root / '.codex/skills'
    suite = json.loads((Path(__file__).parent / 'quality/pilot.json').read_text(encoding='utf-8'))
    dirs = {}
    for name in dict.fromkeys(c['skill'] for c in suite['cases']):
        folder = skills_root / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(
            '---\nname: ' + name + '\ndescription: 品質面板示範資料；不是原始技能，也未呼叫模型。\n---\n'
            '# Synthetic fixture\nThis is not the real skill implementation.\n', encoding='utf-8')
        dirs[name] = folder
    extra = skills_root / 'untested-demo'
    extra.mkdir()
    (extra / 'SKILL.md').write_text('---\nname: untested-demo\ndescription: 尚無評估結果的示範技能\n---\n# Untested\n', encoding='utf-8')
    plan = prepare(suite, dirs, 'DEMO-NO-MODEL')
    replies = []
    for case in suite['cases']:
        answer = {check['path']: check['value'] for check in case['checks']}
        if case['id'] == 'clock-offset':
            answer['delta_seconds'] = 99  # Deliberately wrong, visibly fails the check.
        replies.append(dict(id=case['id'], model='DEMO-NO-MODEL', status='completed', answer=json.dumps(answer)))
    report = root / 'report.json'
    write_json(report, grade(plan, replies))
    (dirs['agent-control'] / 'changed-after-test.txt').write_text('Demonstrate stale result', encoding='utf-8')
    usage = root / 'usage.json'
    write_json(usage, usage_fixture())
    return Dashboard(skills_root, [('品質與使用示範（合成資料）', skills_root),
                                   ('Pi（尚無使用統計）', root / '.pi/skills')],
                     root / 'state', quality_report=report, usage_report=usage)


def usage_fixture(now=None):
    """Explicit synthetic aggregates for a portable demo; never real user history."""
    now = now or datetime.now(timezone.utc)
    last = (now - timedelta(days=1)).isoformat()
    return dict(schema_version=1, source='codex-session-logs', metric='confirmed-skill-reads',
                scope='skill-name-across-local-codex-sessions', generated_at=now.isoformat(),
                window_end=now.isoformat(), is_fixture=True,
                coverage=dict(files_scanned=4, malformed_lines=0, unreadable_files=0,
                              oversized_lines=0, unconfirmed_reads=0, duplicate_events=1, inherited_events=0),
                skills={'failure-triage': {'7': dict(reads=3, conversations=2, last_read=last),
                                          '30': dict(reads=8, conversations=4, last_read=last)}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='skillctl-quality-demo-') as folder:
        server = make_server(create_fixture(folder), args.port)
        url = 'http://127.0.0.1:' + str(server.server_port)
        print(url, flush=True)
        print('管理技能 → 選取 failure-triage → 使用紀錄 7／30 天與文字模擬品質。此為合成資料。', flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
