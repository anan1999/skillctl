"""Verify portable skill locking using two isolated machine directories."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from skillctl import Manager, fingerprint, write_json

REPO = Path(__file__).resolve().parent


def run_demo(root):
    root = Path(root).resolve()
    if any(root.iterdir()):
        raise ValueError('Choose an empty demo directory')
    source = root/'upstream'; source.mkdir()
    names = ('example-skill', 'helper-skill')
    for name in names:
        folder = source/name; folder.mkdir()
        (folder/'SKILL.md').write_bytes(f'---\nname: {name}\ndescription: Example handoff\n---\nRecord verified checks.\n'.encode())
        (folder/'notes.txt').write_bytes(b'Original upstream version.\n')
    def git(*args):
        result = subprocess.run(['git','-C',str(source),*args],capture_output=True,text=True,timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()
    git('init'); git('add','.')
    git('-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-m','version one')
    revision = git('rev-parse','HEAD')
    target_a, target_b = root/'machine-a/.pi/skills', root/'machine-b/.agents/skills'
    empty = root/'empty-source'; empty.mkdir()
    for machine, src, target in (('a',source,target_a), ('b',empty,target_b)):
        write_json(root/(machine+'.json'), {'schema_version':1,'repo':str(REPO),'source':str(src),
                   'state':str(root/('state-'+machine)), 'targets':{'Project':str(target)}})
    manager = Manager(source,target_a,root/'state-a')
    for name in names:
        manager.install(name)
    local = target_a/'example-skill/notes.txt'
    local.write_bytes(local.read_bytes()+b'Always preserve the project review checklist.\n')
    frozen = fingerprint(target_a)
    checks = []
    def cli(machine, *args, expected=0):
        result = subprocess.run([sys.executable,'-B','-X','utf8',str(REPO/'skill_manage.py'),
                                 '--config',str(root/(machine+'.json')),*args],
                                cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=60)
        if result.returncode != expected:
            raise RuntimeError(result.stdout+result.stderr)
        return json.loads(result.stdout)
    def checked(name, condition):
        if not condition:
            raise RuntimeError('Acceptance check failed: '+name)
        checks.append(name)
        print('PASS: '+name)
    file_a, file_b = root/'machine-a/skillctl.lock.json', root/'machine-b/skillctl.lock.json'
    plan = cli('a','lock','create','--file',str(file_a))
    checked('create preview changes no skills and writes no lockfile', not file_a.exists() and fingerprint(target_a)==frozen)
    cli('a','lock','apply',plan['plan_id'])
    checked('lockfile carries the project customization', any(row['customized'] for row in plan['skills']))
    (source/'example-skill/notes.txt').write_bytes(b'Upstream version two.\n')
    git('add','.'); git('-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-m','version two')
    file_b.parent.mkdir(parents=True); shutil.copy2(file_a,file_b)
    # Make the original path unavailable. B uses only the copied lockfile.
    away=root/'upstream-unavailable'; source.rename(away)
    inspect = cli('no-such-config','lock','inspect','--file',str(file_b))
    checked('pins keep the installed Git revision after upstream advances', all(row['source_revision']['commit']==revision for row in inspect['skills']))
    preview = cli('b','lock','restore','--file',str(file_b))
    checked('restore preview writes no installed skills', not target_b.exists())
    restored = cli('b','lock','apply',preview['plan_id'])
    checked('machine B restores exact bytes without the original source', restored['matches_lock'] and fingerprint(target_b)==frozen)
    checked('lock check confirms both skills', cli('b','lock','check','--file',str(file_b))['ok'])
    normal = Manager(away,target_b,root/'state-b')
    try:
        normal.install('example-skill')
    except ValueError as error:
        checked('ordinary updates still protect the carried custom rule', 'customizations' in str(error))
    else:
        raise RuntimeError('A customized skill was overwritten')
    edited=target_b/'example-skill/notes.txt'; edited.write_bytes(edited.read_bytes()+b'Machine B local change.\n')
    before=fingerprint(target_b)
    blocked=cli('b','lock','restore','--file',str(file_b),expected=1)
    checked('new local edits block restore without partial changes', not blocked['ok'] and blocked['plan_id'] is None and fingerprint(target_b)==before)
    kept=cli('b','lock','restore','--file',str(file_b),'--keep-local')
    checked('keep-local leaves bytes intact and reports nonmatching content', kept['ok'] and not kept['matches_lock'] and fingerprint(target_b)==before)
    checked('lock check reports divergence instead of success', not cli('b','lock','check','--file',str(file_b),expected=1)['ok'])
    value=json.loads(file_b.read_text(encoding='utf-8'))
    next(iter(value['objects'].values()))['text']='tampered'
    tampered=root/'tampered.lock.json'; write_json(tampered,value)
    error=cli('b','lock','restore','--file',str(tampered),expected=2)
    checked('tampered objects are rejected before target writes', 'hash mismatch' in error['error'] and fingerprint(target_b)==before)
    result={'ok':True,'checks':checks,'source_revision':revision,
            'scope':'Two isolated Windows directories; no agent runtime, device, network fetch or model calls.'}
    write_json(root/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='New empty directory in which to retain the fixture and evidence')
    args=parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True,exist_ok=False)
        result=run_demo(args.output)
        print(args.output.resolve()/'result.json')
    else:
        with tempfile.TemporaryDirectory(prefix='lockdemo-') as temp:
            result=run_demo(Path(temp))
    print(f"PASS: {len(result['checks'])} lockfile acceptance checks")


if __name__=='__main__':
    main()
