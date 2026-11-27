"""A 30-second walkthrough: update a skill without losing local instructions."""
import argparse
from html import escape
import json
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import webbrowser

from skillctl import Manager, fingerprint

SKILL = 'example-skill'
LOCAL = 'Always include the reviewer before handing off this project.'
UPSTREAM = '# Project handoff with release notes'


def run_demo(root):
    root = Path(root)
    source = root/'source'
    source.mkdir()
    (source/'catalog.json').write_text(json.dumps({'schema': 1, 'skills': [SKILL]}), encoding='utf-8')
    copytree(Path(__file__).resolve().parent/'skills'/SKILL, source/'skills'/SKILL)
    source_path = source/'skills'/SKILL/'SKILL.md'
    # Keep this synthetic fixture's newlines stable across Git checkout settings.
    source_path.write_bytes(source_path.read_text(encoding='utf-8').encode('utf-8'))
    laptop, server = [Manager(source, root/name/'skills', root/name/'state') for name in ('laptop', 'server')]
    for machine in (laptop, server):
        machine.install(SKILL)
    initial = fingerprint(laptop.target/SKILL)
    if initial != fingerprint(server.target/SKILL):
        raise RuntimeError('The initial installations differ')
    local_path = server.target/SKILL/'SKILL.md'
    source_path = source/'skills'/SKILL/'SKILL.md'
    base = local_path.read_text(encoding='utf-8')
    if '# Project handoff' not in base:
        raise RuntimeError('Demo fixture heading changed')
    local_path.write_bytes((base.rstrip()+'\n\n'+LOCAL+'\n').encode('utf-8'))
    source_path.write_bytes(base.replace('# Project handoff', UPSTREAM).encode('utf-8'))
    local = local_path.read_text(encoding='utf-8')
    before_block = fingerprint(server.target/SKILL)
    try:
        server.install(SKILL)
    except ValueError as error:
        if not str(error).startswith('Local drift;'):
            raise
        blocked_reason = str(error)
    else:
        raise RuntimeError('Expected update to protect the local instructions')
    if fingerprint(server.target/SKILL) != before_block:
        raise RuntimeError('Blocked update changed the local files')
    comparison = server.diff(SKILL, include_text=True)
    preview = server.reconcile(SKILL, 'merge')
    if not preview['ok'] or fingerprint(server.target/SKILL) != before_block:
        raise RuntimeError('Merge preview failed or changed files')
    applied = server.reconcile(SKILL, 'merge', apply=True)
    merged = local_path.read_text(encoding='utf-8')
    if not applied['ok'] or LOCAL not in merged or UPSTREAM not in merged:
        raise RuntimeError('Merge did not preserve both independent changes')
    if fingerprint(laptop.target/SKILL) != initial:
        raise RuntimeError('The other installation was changed')
    changed = [row for row in comparison['files'] if row['file'] == 'SKILL.md']
    if len(changed) != 1:
        raise RuntimeError('Expected a SKILL.md difference')
    return dict(skill=SKILL, base=base, local=local, merged=merged,
                local_instruction=LOCAL, upstream_heading=UPSTREAM,
                blocked_reason=blocked_reason, patch=changed[0]['local_vs_upstream']['diff'],
                hashes=dict(original=initial['SKILL.md'], local=before_block['SKILL.md'],
                            merged=fingerprint(server.target/SKILL)['SKILL.md']),
                checks=['Initial copies matched', 'Update blocked without changing local files',
                        'Merge preview did not change files', 'Merge retained local and upstream changes',
                        'Other installation unchanged'])


def report(result):
    local = escape(result['local_instruction'])
    upstream = escape(result['upstream_heading'])
    hashes = ''.join(f'<dt>{escape(name)}</dt><dd><code>{escape(value)}</code></dd>'
                     for name, value in result['hashes'].items())
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>skillctl · Keep your changes</title>
<style>
:root {{color-scheme:light;--ink:#132d2b;--muted:#526562;--line:#d3dfd8;--accent:#155c48}}
* {{box-sizing:border-box}} body {{margin:0;background:#f3f5ef;color:var(--ink);font:16px/1.65 system-ui,sans-serif}}
main {{max-width:1080px;margin:auto;padding:36px 28px 52px}}
.brand {{display:flex;justify-content:space-between;gap:20px;font-size:13px;font-weight:700;letter-spacing:.07em}}
.badge {{color:var(--accent)}} h1 {{font-size:clamp(34px,5vw,57px);line-height:1.08;letter-spacing:-.045em;margin:35px 0 18px}}
.lead {{max-width:770px;color:var(--muted);font-size:18px;margin-bottom:28px}}
nav {{display:flex;gap:10px;flex-wrap:wrap;margin:25px 0 16px}} button {{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:9px;background:white;padding:11px 16px;color:var(--ink)}}
button[aria-pressed="true"] {{background:var(--ink);color:white;border-color:var(--ink)}} button:focus-visible {{outline:3px solid #d79b22;outline-offset:3px}}
section {{background:white;border:1px solid var(--line);border-radius:14px;padding:26px}}
h2 {{font-size:24px;line-height:1.3;margin:0 0 12px}} p {{margin:10px 0}} .label {{font-size:12px;text-transform:uppercase;letter-spacing:.08em;font-weight:700;color:var(--muted)}}
.columns {{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:22px}} .note {{padding:18px;border:1px solid var(--line);border-radius:9px;background:#f7f9f5}}
.local {{border-left:4px solid #b78022}} .upstream {{border-left:4px solid #37866b}} .result {{background:#e6f2e9;padding:16px;border-radius:9px;margin-top:20px}}
.blocked {{color:#8a4b14}} code,pre {{font-family:ui-monospace,Consolas,monospace;font-size:14px}} pre {{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f1;padding:18px;border-radius:8px}}
details {{margin-top:18px}} summary {{cursor:pointer}} dd {{margin:4px 0 12px;overflow-wrap:anywhere}} .bottom {{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:18px}}
.small {{font-size:13px;color:var(--muted)}} .next {{margin-top:26px;border-top:1px solid var(--line);padding-top:18px}} [hidden] {{display:none!important}}
@media(max-width:640px) {{main {{padding:22px 16px}} .columns {{grid-template-columns:1fr}} section {{padding:19px}} .brand,.bottom {{flex-wrap:wrap}}}}
</style></head><body><main>
<div class="brand"><span>SKILLCTL / 30-SECOND DEMO</span><span class="badge">5 FILE CHECKS PASSED</span></div>
<h1>Update your AI skills.<br>Keep your changes.</h1>
<p class="lead">You added a project rule. A new upstream version arrived. See what happens next.</p>
<nav aria-label="Demo steps">
<button type="button" data-step="0" aria-pressed="true" aria-controls="step-0">1 · Your change</button>
<button type="button" data-step="1" aria-pressed="false" aria-controls="step-1">2 · Update blocked</button>
<button type="button" data-step="2" aria-pressed="false" aria-controls="step-2">3 · Keep both</button>
</nav>
<section id="step-0" aria-labelledby="heading-0"><div class="label">The setup</div>
<h2 id="heading-0">One skill. Two independent changes.</h2>
<p>Two temporary directories started with the same skill. You edited one copy; the source then changed.</p>
<div class="columns"><div class="note local"><div class="label">Your rule · SKILL.md</div><p><code>+ {local}</code></p></div>
<div class="note upstream"><div class="label">Upstream update · SKILL.md</div><p><code>+ {upstream}</code></p></div></div></section>
<section id="step-1" aria-labelledby="heading-1"><div class="label">Protection</div>
<h2 id="heading-1" class="blocked">The update stops. Your rule stays.</h2>
<p>skillctl found your local change in <code>SKILL.md</code>. Replacing the file would remove it, so the ordinary update was blocked.</p>
<pre>{escape(result['patch'])}</pre>
<details><summary>Exact block reason</summary><p>{escape(result['blocked_reason'])}</p></details></section>
<section id="step-2" aria-labelledby="heading-2"><div class="label">After previewing a merge</div>
<h2 id="heading-2">Your rule stays. The update arrives.</h2>
<div class="result"><strong>Your instructions, with the upstream update</strong><p><code>{upstream}</code></p><p><code>{local}</code></p></div>
<p>These edits affect different lines, so the merge succeeded. The other installation stayed unchanged.</p>
<p>Overlapping edits stop the merge so you can decide how to resolve them.</p>
<details><summary>Inspect the merged SKILL.md</summary><pre>{escape(result['merged'])}</pre></details></section>
<div class="bottom"><span class="small" id="progress" aria-live="polite">Real file operations in disposable directories</span><button type="button" id="next" hidden>Next →</button></div>
<details><summary>Verification evidence</summary><ul>{''.join('<li>'+escape(item)+'</li>' for item in result['checks'])}</ul><dl>{hashes}</dl>
<p class="small">Synthetic example content; actual install, block, preview and merge checks. No agent, model or device was run. Temporary skill directories were removed after the run.</p></details>
<div class="next"><strong>Try your own skills</strong><pre>python -X utf8 skill_manage.py init</pre>
<p class="small">Run in the skillctl checkout. Choose your source and agent directory, then review before saving. Initialization changes no skills.</p>
<p class="small">No server. No API key. Works offline.</p></div>
</main><script>
const buttons = [...document.querySelectorAll('[data-step]')];
const panels = buttons.map((button) => document.getElementById(button.getAttribute('aria-controls')));
const next = document.getElementById('next'); let step = 0;
function show(value) {{step=value; panels.forEach((panel,index)=>panel.hidden=index!==step);
buttons.forEach((button,index)=>button.setAttribute('aria-pressed',String(index===step)));
next.textContent=step===2?'Replay ↺':'Next →';
document.getElementById('progress').textContent=(step+1)+' / 3 · Actual file checks';}}
buttons.forEach((button,index)=>button.addEventListener('click',()=>show(index)));
next.addEventListener('click',()=>show((step+1)%3)); next.hidden=false; show(0);
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('skillctl-demo.html'))
    parser.add_argument('--open', action='store_true', help='Open the offline report in your browser')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Report already exists; choose another --output filename.')
    with TemporaryDirectory(prefix='skillctl-demo-') as directory:
        result = run_demo(Path(directory))
    with output.open('x', encoding='utf-8') as stream:
        stream.write(report(result))
    print('1. You added a local rule to SKILL.md.')
    print('2. An ordinary update was blocked; the file stayed intact.')
    print('3. Preview + merge kept your rule and the upstream update.')
    print('PASS: 5 file checks; other installation unchanged. No agent/model/device calls.')
    print(output)
    if args.open:
        webbrowser.open(output.as_uri())


if __name__ == '__main__':
    main()
