"""A disposable, headless learning/impact/comparison walkthrough. No model calls."""
import argparse
import json
from pathlib import Path
import sys

from skillctl import Manager, fingerprint, write_json
from skill_workflow import fresh_directory, record_learning, learning_receipts, impact, prepare_comparison, run_comparison


def prepare_demo(output):
    root = fresh_directory(output)
    old, new = root/'old/example-skill', root/'new/example-skill'
    old.mkdir(parents=True); new.mkdir(parents=True)
    header = '---\nname: example-skill\ndescription: Classify clock diagnostics from supplied text evidence.\n---\n'
    (old/'SKILL.md').write_text(header+'When both device timestamps are present, calculate the difference. Otherwise report zero difference.\n',encoding='utf-8')
    (new/'SKILL.md').write_text(header+'When both device timestamps with UTC offsets are present, calculate board minus camera in seconds. If either timestamp is missing, report unknown (null), never zero. Use supplied text only; do not change devices.\n',encoding='utf-8')
    suite = {'schema_version':1,'id':'workflow-demo','cases':[
        {'id':'known-time','skill':'example-skill','kind':'calculation','task':'Give board minus camera in seconds.',
         'input':'Board: 2026-01-01T12:00:05+08:00; camera: 2026-01-01T12:00:00+08:00.',
         'response_schema':{'difference_seconds':'Integer seconds, or null if unknown.'},
         'checks':[{'path':'difference_seconds','op':'eq','value':5}]},
        {'id':'missing-time','skill':'example-skill','kind':'missing-evidence','task':'Give board minus camera in seconds.',
         'input':'Board: 2026-01-01T12:00:05+08:00; camera timestamp was not collected.',
         'response_schema':{'difference_seconds':'Integer seconds, or null if unknown.'},
         'checks':[{'path':'difference_seconds','op':'eq','value':None}]}]}
    write_json(root/'suite.json',suite)
    write_json(root/'manager.json',{'schema_version':1,'repo':str(Path(__file__).resolve().parent),
        'source':str(root/'new'),'state':str(root/'state'),'targets':{'Demo':str(root/'target')}})
    # Use the old version as the initially managed baseline, then introduce a local edit.
    initial=Manager(root/'old',root/'target',root/'state');initial.install('example-skill')
    local=root/'target/example-skill/SKILL.md'
    local.write_text(local.read_text(encoding='utf-8')+'Preserve the operator note in the report.\n',encoding='utf-8')
    manager=Manager(root/'new',root/'target',root/'state')
    before=fingerprint(manager.target)
    summary=impact(manager,'example-skill');write_json(root/'impact.json',summary)
    lesson={'schema_version':1,'skill':'example-skill','scope':{'kind':'project','path':str(root)},
            'trigger':'相機時間未取得時','lesson':'缺少時間證據應回報未知，不當成零時差。',
            'decision':'update','reason':'既有 example-skill 已負責時間證據，先補強它。',
            'evidence':[{'path':str(new/'SKILL.md'),'summary':'候選文字，尚不代表模型或裝置已驗證。'}]}
    write_json(root/'learning-input.json',lesson)
    receipt=record_learning(manager,lesson);write_json(root/'learning-receipt.json',receipt)
    assert learning_receipts(manager)['receipts'][0]['evidence_now'][0]['status']=='matched'
    assert fingerprint(manager.target)==before
    return root,old,new,suite


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('.demo/workflow'))
    parser.add_argument('--fixture',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.fixture:
        request=json.load(sys.stdin)
        answer=5 if request['id']=='known-time' else None if 'never zero' in request['context'] else 0
        print(json.dumps({'id':request['id'],'model':request['model'],'request_sha256':request['request_sha256'],
            'status':'completed','answer':{'difference_seconds':answer}}))
        return 0
    root,old,new,suite=prepare_demo(args.output)
    plan=prepare_comparison(suite,old,new,'DEMO-NO-MODEL',max_calls=4)
    write_json(root/'plan.json',plan)
    adapter={'kind':'fixture','argv':[sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--fixture']}
    write_json(root/'adapter.json',adapter)
    result=run_comparison(plan,adapter,root/'comparison')
    assert result['ok'] and result['is_fixture']
    assert [p['verdict'] for p in result['pairs']]==['unchanged','improved']
    print(json.dumps({'ok':True,'is_fixture':True,'output':str(root),'pairs':result['pairs'],
        'note':'演示資料：無模型呼叫、無服務、無裝置操作。已示範提煉收據、更新阻擋與成對比較。'},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__': raise SystemExit(main())
