"""Locate the explicitly configured skillctl checkout and run a single command."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--config', type=Path, default=Path.home()/'.config/skillctl/manager.json')
    args, command = parser.parse_known_args(argv)
    try:
        repo = args.repo or os.environ.get('SKILLCTL_REPO')
        if repo is None and args.config.is_file():
            with args.config.open('rb') as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError('Configuration exceeds 64 KiB')
            config = json.loads(data.decode('utf-8-sig'))
            repo = config.get('repo')
        if repo is None:
            repo = next((p for p in Path(__file__).resolve().parents
                         if (p/'skill_manage.py').is_file() and (p/'skillctl.py').is_file()), None)
        if repo is None:
            raise ValueError('Set repo in ~/.config/skillctl/manager.json or supply --repo /path/to/skillctl')
        repo = Path(repo).expanduser()
        if not repo.is_absolute() or not all((repo/name).is_file() for name in
                ('skill_manage.py', 'skillctl.py', 'skillctl_content.py', 'skill_usage.py', 'skill_quality.py')):
            raise ValueError('Expected an absolute path to the installed skillctl checkout')
        return subprocess.run([sys.executable, '-B', '-X', 'utf8', str(repo/'skill_manage.py'),
                               '--config', str(args.config), *(command or ['--help'])], check=False).returncode
    except (OSError, ValueError, AttributeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
