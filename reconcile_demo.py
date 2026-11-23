"""Exercise scan -> adopt -> blocked update -> merge -> conflict -> keep -> rollback."""
from pathlib import Path
import shutil
import tempfile

from skillctl import Manager, fingerprint


def main():
    with tempfile.TemporaryDirectory(prefix='skillctl-reconcile-') as temp:
        root = Path(temp)
        source = root / 'source'
        skill = source / 'example-skill'
        shutil.copytree(Path(__file__).parent / 'skills/example-skill', skill)
        notes = 'first\nsecond\nthird\nfourth\nfifth\nsixth\nlast\n'
        (skill / 'notes.txt').write_text(notes, encoding='utf-8')
        target = root / 'project/.pi/skills'
        shutil.copytree(skill, target / 'example-skill')
        local = target / 'example-skill/notes.txt'
        local.write_text(notes.replace('first', 'LOCAL'), encoding='utf-8')
        manager = Manager(source, target, root / 'state')
        assert manager.scan()[0]['status'] == 'unmanaged-conflict'
        before = fingerprint(target / 'example-skill')
        manager.adopt('example-skill', apply=True)
        assert fingerprint(target / 'example-skill') == before
        print('1. Scanned and adopted existing skill without changing local files.')
        (skill / 'notes.txt').write_text(notes.replace('last', 'UPSTREAM'), encoding='utf-8')
        try:
            manager.install('example-skill')
        except ValueError as error:
            print('2. Update blocked:', error)
        else:
            raise AssertionError('Install should protect customizations')
        for file in manager.diff('example-skill', include_text=True)['files']:
            print(file['local_vs_upstream']['diff'])
        result = manager.reconcile('example-skill', 'merge', apply=True)
        assert result['ok'] and 'LOCAL' in local.read_text() and 'UPSTREAM' in local.read_text()
        print('3. Three-way merge retained LOCAL and incorporated UPSTREAM.')
        local.write_text('local conflict\n', encoding='utf-8')
        (skill / 'notes.txt').write_text('upstream conflict\n', encoding='utf-8')
        before = fingerprint(target / 'example-skill')
        result = manager.reconcile('example-skill', 'merge', apply=True)
        assert not result['ok'] and fingerprint(target / 'example-skill') == before
        print('4. Overlapping changes blocked without changing the installed skill.')
        print(result['conflicts'][0]['preview'])
        manager.reconcile('example-skill', 'keep', apply=True)
        assert fingerprint(target / 'example-skill') == before
        print('5. Keep acknowledged the update and preserved all local bytes.')
        manager.rollback('example-skill')
        assert fingerprint(target / 'example-skill') == before
        assert manager.listing()[0]['status'] == 'local-drift'
        print('6. Rollback restored the previous receipt/baseline; local edits remain intact.')
        print('PASS: all checks completed in disposable directories.')


if __name__ == '__main__':
    main()
