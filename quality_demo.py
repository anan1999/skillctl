"""Show quality states using disposable synthetic skills; no model or device calls."""
import argparse
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
    suite = json.loads((Path(__file__).parent / 'quality/pilot.json').read_text(encoding='utf-8'))
    dirs = {}
    for name in dict.fromkeys(c['skill'] for c in suite['cases']):
        folder = root / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(
            '---\nname: ' + name + '\ndescription: 品質面板示範資料；不是原始技能，也未呼叫模型。\n---\n'
            '# Synthetic fixture\nThis is not the real skill implementation.\n', encoding='utf-8')
        dirs[name] = folder
    extra = root / 'skills/untested-demo'
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
    return Dashboard(root / 'skills', [('品質示範（無模型呼叫）', root / 'skills')], root / 'state', quality_report=report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='skillctl-quality-demo-') as folder:
        server = make_server(create_fixture(folder), args.port)
        url = 'http://127.0.0.1:' + str(server.server_port)
        print(url, flush=True)
        print('管理技能 → 選取技能 → 文字模擬品質。此為 fixture，不是模型實測。', flush=True)
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
