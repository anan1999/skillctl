"""Exercise a multi-target upgrade, conflicts, preservation, rollback and evidence drift.

All mutations are confined to a new disposable output directory. No model or
real agent applications/devices are used by this acceptance walkthrough.
"""
import argparse
from pathlib import Path
import json

from skillctl import Manager, fingerprint, write_json
from skill_manage import Management, load_config
from skill_workflow import fresh_directory, impact, record_learning, learning_receipts


def exercise(output):
    root=fresh_directory(output)
    source=root/'source'; skill=source/'example-skill'
    (skill/'references').mkdir(parents=True); (skill/'scripts').mkdir()
    (skill/'SKILL.md').write_text('---\nname: example-skill\ndescription: A local evidence workflow example\n---\n'
        'Read [runbook](references/runbook.md).\n',encoding='utf-8')
    (skill/'references/runbook.md').write_text('Keep original evidence.\nReport unknowns.\n',encoding='utf-8')
    (skill/'scripts/check.py').write_text('print("version one")\n',encoding='utf-8')
    codex=root/'project/.agents/skills'; pi=root/'project/.pi/skills'
    config=root/'manager.json'
    write_json(config,{'schema_version':1,'source':str(source),'state':str(root/'state'),
                      'targets':{'Codex':str(codex),'Pi':str(pi)}})
    app=Management(load_config(config))
    events=[]
    def record(name, **details):
        events.append({'check':name,'passed':True,**details})
        write_json(root/'progress.json',{'checks':events})
    def apply(action,target='Codex'):
        preview=app.preview(action,'example-skill',target)
        assert preview['ok'] and preview['plan_id'],preview
        return app.apply(preview['plan_id'],target)
    for label in ('Codex','Pi'): apply('install',label)
    pi_before=fingerprint(pi)
    local=codex/'example-skill'
    (local/'references/runbook.md').write_text('Keep operator annotations and original evidence.\nReport unknowns.\n',encoding='utf-8')
    local_before=fingerprint(local)
    (skill/'scripts/check.py').write_text('print("version two")\n',encoding='utf-8')
    blocked=app.preview('update','example-skill','Codex')
    assert not blocked['ok'] and blocked['plan_id'] is None
    assert fingerprint(local)==local_before
    _,manager=app.manager('Codex')
    changes=impact(manager,'example-skill'); write_json(root/'impact.json',changes)
    assert changes['blocked_reason'] and any(f['review_removed_guidance'] for f in changes['files'])
    record('ordinary-update-blocked-with-local-evidence-preserved')

    preview=app.preview('merge','example-skill','Codex')
    assert preview['ok']
    (skill/'late.txt').write_text('Source changed after preview.',encoding='utf-8')
    try: app.apply(preview['plan_id'],'Codex')
    except ValueError as error: assert 'stale' in str(error)
    else: raise AssertionError('Changed source was not rejected')
    assert fingerprint(local)==local_before
    record('source-change-invalidates-preview-with-no-partial-write')
    (skill/'late.txt').unlink()  # Only the exact file just created in this disposable workspace.
    apply('merge')
    merged=fingerprint(local)
    assert (local/'scripts/check.py').read_bytes()==(skill/'scripts/check.py').read_bytes()
    assert (local/'references/runbook.md').read_text(encoding='utf-8').startswith('Keep operator annotations')
    assert app.doctor('example-skill','Codex')['ok']
    assert fingerprint(pi)==pi_before
    record('nonoverlapping-merge-preserves-local-guidance-and-isolates-pi')

    (skill/'references/runbook.md').write_text('Discard old annotations.\nReport unknowns.\n',encoding='utf-8')
    conflict=app.preview('merge','example-skill','Codex')
    assert not conflict['ok'] and conflict['plan_id'] is None and conflict['result']['conflicts']
    assert fingerprint(local)==merged
    write_json(root/'conflict.json',conflict)
    record('overlapping-edit-conflict-blocks-entire-merge')
    apply('keep')
    assert fingerprint(local)==merged
    assert not app.preview('update','example-skill','Codex')['ok']
    record('keep-local-retains-bytes-and-does-not-enable-overwrite')
    apply('rollback') # Undo keep and restore its previous upstream baseline.
    assert fingerprint(local)==merged
    assert not app.preview('merge','example-skill','Codex')['ok']
    apply('rollback') # Undo merge; restore original local guidance plus old script.
    assert fingerprint(local)==local_before
    assert fingerprint(pi)==pi_before
    record('rollback-restores-content-and-baseline')

    # A plan for one location cannot be applied to the other.
    preview=app.preview('update','example-skill','Pi')
    assert preview['ok'] and preview['plan_id']
    try: app.apply(preview['plan_id'],'Codex')
    except (ValueError,FileNotFoundError): pass
    else: raise AssertionError('Cross-target plan accepted')
    assert fingerprint(pi)==pi_before and fingerprint(local)==local_before
    record('cross-target-plan-rejected')

    evidence=root/'verified-evidence.json'
    write_json(evidence,{'scope':'isolated-file-management','checks':events})
    receipt=record_learning(manager,{'schema_version':1,'skill':'example-skill',
        'scope':{'kind':'project','path':str(root/'project')},'trigger':'上游和本機都修改技能',
        'lesson':'先預覽；保留本機指引；有衝突時不能以安裝覆蓋。','decision':'update',
        'reason':'既有技能可補強，不重複建立。','evidence':[{'path':str(evidence),'summary':'隔離檔案管理實測，不是裝置驗收。'}]})
    assert receipt['receipt']['status']=='observed' and not receipt['receipt']['installed']
    assert learning_receipts(manager,receipt['receipt']['id'])['receipts'][0]['evidence_now'][0]['status']=='matched'
    write_json(evidence,{'scope':'changed evidence'})
    assert learning_receipts(manager,receipt['receipt']['id'])['receipts'][0]['evidence_now'][0]['status']=='changed'
    record('learning-receipt-detects-evidence-drift-without-promoting-claims')
    result={'ok':True,'checks':events,'targets_untouched_except_authorized_demo_operations':True,
            'scope':'isolated file operations; no model, harness runtime or device execution'}
    write_json(root/'report.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(exercise(args.output),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
