"""Try the management page with disposable skills; no personal skills are touched."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import webbrowser

from skillctl import Manager
from skill_dashboard import Dashboard, make_server


def build_demo(root):
    source, target, state = root / 'source', root / 'project/.pi/skills', root / 'state'
    for name in ('adopt-demo', 'merge-demo', 'conflict-demo', 'stale-demo', 'missing-demo'):
        folder = source / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: 相機時間診斷 測試技能\n---\n# Demo\nRead notes.txt.\n', encoding='utf-8')
        (folder / 'notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nlast\n', encoding='utf-8')
    manager = Manager(source, target, state)
    for name in ('merge-demo', 'conflict-demo', 'stale-demo'):
        manager.install(name)
    shutil.copytree(source / 'adopt-demo', target / 'adopt-demo')
    (target / 'adopt-demo/local.txt').write_text('keep existing notes', encoding='utf-8')
    (target / 'merge-demo/notes.txt').write_text('LOCAL\ntwo\nthree\nfour\nfive\nsix\nlast\n', encoding='utf-8')
    (source / 'merge-demo/notes.txt').write_text('first\ntwo\nthree\nfour\nfive\nsix\nUPSTREAM\n', encoding='utf-8')
    (target / 'conflict-demo/notes.txt').write_text('LOCAL conflict\n', encoding='utf-8')
    (source / 'conflict-demo/notes.txt').write_text('UPSTREAM conflict\n', encoding='utf-8')
    return Dashboard(source, [('Pi demo', target), ('Codex demo', root / 'project/.agents/skills')], state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--info', type=Path, help='Write fixture paths/URL for the optional browser test; new file only')
    args = parser.parse_args()
    if args.info and args.info.exists():
        parser.error('--info file already exists; choose a fresh filename')
    with tempfile.TemporaryDirectory(prefix='skillctl-dashboard-demo-') as directory:
        root = Path(directory)
        server = make_server(build_demo(root))
        info = {'fixture': True, 'root': str(root), 'url': 'http://127.0.0.1:' + str(server.server_port)}
        if args.info:
            with args.info.open('x', encoding='utf-8') as out:
                json.dump(info, out)
        print(json.dumps(info), flush=True)
        if args.open:
            webbrowser.open(info['url'])
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
