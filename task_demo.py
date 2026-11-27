"""Offline demonstration of candidate trials, remembered failures and recipe replay.

The adapter is deliberately deterministic. This proves workflow behavior, not AI
quality. All Python programs used here are synthetic and trusted.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

import skill_task as task
from skill_manage import Management, load_config
from skillctl import fingerprint, write_json

GOOD = '''import math
def parse(row):
    value = row.get('reading')
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number != -9999 else None
'''
BAD = GOOD.replace(' and number != -9999', '')
BASELINE = "def parse(row):\n    return float(row['reading'])\n"
VERIFIER = '''import copy, json, sys
from parser import parse
case = sys.argv[1]
try:
    if case == 'numeric':
        values = json.load(open('samples.json', encoding='utf-8'))['readings']
        passed = all(parse({'reading': x}) == float(x) for x in values)
    elif case == 'invalid':
        passed = all(parse(x) is None for x in ({}, {'reading':None}, {'reading':True},
            {'reading':'bad'}, {'reading':'nan'}, {'reading':'inf'}))
    elif case == 'sentinel':
        passed = all(parse({'reading':x}) is None for x in (-9999, '-9999', ' -9999.0 '))
    elif case == 'input-stability':
        row = {'reading':'42', 'metadata':{'device':'demo'}}
        before = copy.deepcopy(row)
        parse(row)
        passed = row == before
    else:
        raise RuntimeError('Unknown verifier case')
    detail = case + (' passed' if passed else ' assertion failed')
except (ValueError, TypeError, KeyError, AssertionError) as error:
    passed, detail = False, type(error).__name__ + ': ' + str(error)
print(json.dumps({'passed':passed, 'detail':detail}))
sys.exit(0 if passed else 1)
'''


def fixture(root):
    root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    source=root/'source'; project=root/'project'; project.mkdir()
    for name,instructions in {
        'telemetry-contract':'For telemetry parser tasks, implement parse(row) without mutating row. Read reading with get; missing, None, booleans, malformed strings, nonfinite numbers and the reserved numeric sentinel -9999 (including string forms) mean unavailable: return None. Otherwise return float(value). Never replace invalid values with zero.',
        'telemetry-legacy':'For telemetry parser tasks, implement parse(row) without mutating row. Missing, None, booleans, malformed strings and nonfinite values return None. Otherwise return float(value). All finite numbers, including -9999, are valid readings. Do not filter any finite numeric values.'
    }.items():
        folder=source/name; folder.mkdir(parents=True)
        (folder/'SKILL.md').write_text('---\nname: '+name+'\ndescription: Telemetry parser contract and failure checks\n---\n'+instructions+'\n',encoding='utf-8')
    (project/'parser.py').write_text(BASELINE,encoding='utf-8')
    (project/'verify.py').write_text(VERIFIER,encoding='utf-8')
    write_json(project/'samples.json',{'readings':['12.5',' -2 ','0']})
    spec={'schema_version':1,'id':'telemetry-parser','task':'Repair the telemetry parser parse(row) according to the telemetry contract. Convert numeric readings, handle unavailable readings, and preserve the input row. Keep the parse(row) API.',
          'files':['parser.py','verify.py','samples.json'],'context_files':['parser.py','samples.json'],'write_files':['parser.py'],
          'checks':[{'id':c,'argv':['{python}','verify.py',c],'timeout':5} for c in ('numeric','invalid','sentinel','input-stability')]}
    write_json(root/'spec.json',spec)
    config={'schema_version':1,'source':str(source),'state':str(root/'state'),'targets':{'Demo':str(root/'target/.agents/skills')}}
    write_json(root/'config.json',config)
    adapter={'kind':'fixture','argv':[sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--adapter']}
    write_json(root/'adapter.json',adapter)
    return Management(load_config(root/'config.json')),spec,project,adapter


def demo(root):
    app,spec,project,adapter=fixture(root); root=Path(root).resolve()
    _,manager=app.manager('Demo'); before=fingerprint(project)
    found=task.find(manager,spec,project)
    plan=task.prepare(manager,spec,project,'fixture',['telemetry-contract','telemetry-legacy'])
    write_json(root/'plan.json',plan)
    report=task.run(manager,plan,adapter,root/'trial')
    assert report['ok'], report['halted_reason']
    by_name={r['name']:r for r in report['variants']}
    assert by_name['telemetry-contract']['status']=='passed'
    assert by_name['telemetry-legacy']['status']=='failed'
    assert not any(r['eligible_for_adoption'] for r in report['variants'])
    saved=task.remember(manager,report['report_path'],'telemetry-legacy','sentinel')
    missing={**spec,'checks':[c for c in spec['checks'] if c['id']!='sentinel']}
    try: task.prepare(manager,missing,project,'fixture',['telemetry-contract'])
    except ValueError: blocked=True
    else: blocked=False
    assert blocked, 'Removing remembered check must block a new trial'
    recipe=task.recipe(report['report_path'],'telemetry-contract',root/'recipe.json')['recipe']
    second=root/'another-project'; shutil.copytree(project,second)
    write_json(second/'samples.json',{'readings':['99.25','1e2','-8']})
    replay_plan=task.replay(manager,recipe,second)
    replay=task.run(manager,replay_plan,adapter,root/'replay')
    assert replay['ok'] and replay['variants'][1]['status']=='passed'
    assert before==fingerprint(project)
    result={'ok':True,'is_fixture':True,'candidates':[c['name'] for c in found['candidates']],
            'variants':[{k:r[k] for k in ('name','status','regressions')} for r in report['variants']],
            'remembered_check':saved['guard']['check']['id'],'missing_guard_blocked':blocked,
            'recipe_replay':replay['variants'][1]['status'],'original_unchanged':True,
            'report':report['report_path'],'replay_report':replay['report_path'],
            'note':'Deterministic fixture, not model quality or native harness evidence. No skills installed.'}
    write_json(root/'summary.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--adapter',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--setup-only',action='store_true',help='Create synthetic inputs for a separately configured model adapter')
    args=parser.parse_args()
    if args.adapter:
        request=json.load(sys.stdin)
        code={'baseline':BASELINE,'telemetry-contract':GOOD,'telemetry-legacy':BAD}[request['id']]
        print(json.dumps({k:request[k] for k in ('id','model','request_sha256')} |
                         {'status':'completed','answer':{'files':{'parser.py':code},'summary':'Deterministic demo fixture'}}))
        return
    root=task.fresh_directory(args.output) if args.output else Path(tempfile.mkdtemp(prefix='skill-task-'))
    if args.setup_only:
        fixture(root)
        print(json.dumps({'ok':True,'directory':str(root),'note':'Synthetic inputs only; no model called'}))
    else: print(json.dumps(demo(root),indent=2))


if __name__=='__main__': main()
