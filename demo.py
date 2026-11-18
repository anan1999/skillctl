"""Reproduce and inspect skill drift using two disposable local targets."""

import argparse
from html import escape
from pathlib import Path
from shutil import copytree, copy2
from tempfile import TemporaryDirectory
import webbrowser

from skillctl import Manager, fingerprint


SKILL = 'example-skill'


def run_demo(root):
    project = Path(__file__).resolve().parent
    source = root / 'source'
    source.mkdir()
    copy2(project / 'catalog.json', source / 'catalog.json')
    copytree(project / 'skills' / SKILL, source / 'skills' / SKILL)
    laptop, server = [
        Manager(source, root / name / 'skills', root / name / 'state')
        for name in ('laptop', 'server')
    ]
    for machine in (laptop, server):
        machine.install(SKILL)

    before = fingerprint(laptop.target / SKILL)
    if before != fingerprint(server.target / SKILL):
        raise RuntimeError('The two initial installs differ')

    edited = server.target / SKILL / 'SKILL.md'
    with edited.open('a', encoding='utf-8') as file:
        file.write('\nRecord the reviewer for this local handoff.\n')

    with (source / 'skills' / SKILL / 'SKILL.md').open('a', encoding='utf-8') as file:
        file.write('\nRecord the release version for this handoff.\n')

    try:
        server.install(SKILL)
    except ValueError as error:
        if not str(error).startswith('Local drift;'):
            raise
        blocked_reason = str(error)
    else:
        raise RuntimeError('Expected the installer to block local drift')

    after = fingerprint(server.target / SKILL)
    if not edited.read_text(encoding='utf-8').endswith('local handoff.\n'):
        raise RuntimeError('The local edit was lost')
    if laptop.listing()[0]['status'] != 'update-available' or server.listing()[0]['status'] != 'local-drift':
        raise RuntimeError('Unexpected inventory status')
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed != ['SKILL.md']:
        raise RuntimeError('Expected only SKILL.md to differ')
    return blocked_reason, [(name, before.get(name, 'Missing'), after.get(name, 'Missing')) for name in changed]


def report(blocked_reason, differences):
    rows = ''.join(
        '<tr>' + ''.join('<td><code>' + escape(value) + '</code></td>' for value in row) + '</tr>'
        for row in differences
    )
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>skillctl demo · Local drift</title>
<style>
  body {{ margin: 3rem auto; width: min(62rem, 100% - 2rem); font: 16px/1.6 system-ui, sans-serif; color: #182b29; background: #f2f6f3; }}
  h1 {{ font-size: clamp(2rem, 6vw, 3.5rem); line-height: 1.05; }}
  section {{ background: white; padding: 1rem 1.25rem; margin: 1rem 0; border: 1px solid #cedcd4; border-radius: .75rem; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  th, td {{ text-align: left; padding: .55rem; vertical-align: top; }}
  th {{ background: #e2eee8; }}
  td {{ overflow-wrap: anywhere; }}
  th:first-child {{ width: 17%; }}
  .blocked {{ color: #873a16; }}
</style>
<main>
  <h1>One skill, two machines</h1>
  <p>Both local targets started with identical copies of <code>{SKILL}</code>.</p>
  <section>
    <h2 class="blocked">Server update blocked</h2>
    <p>{escape(blocked_reason)}</p>
    <p>The source has an update. Laptop is still on the original install. The server's file differs from its receipt, so the installer preserved the local edit.</p>
  </section>
  <section>
    <h2>Changed file</h2>
    <table>
      <thead><tr><th scope="col">File</th><th scope="col">Laptop SHA-256</th><th scope="col">Server SHA-256</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
</main>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('skillctl-demo.html'))
    parser.add_argument('--open', action='store_true', help='Open the report in your browser')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='skillctl-demo-') as directory:
        blocked_reason, differences = run_demo(Path(directory))
    output = args.output.resolve()
    with output.open('x', encoding='utf-8') as file:
        file.write(report(blocked_reason, differences))
    print('Verified: initial copies matched; SKILL.md drifted; update blocked; edit preserved.')
    print(output)
    if args.open:
        webbrowser.open(output.as_uri())


if __name__ == '__main__':
    main()
