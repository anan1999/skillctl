import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import skill_init
from skill_manage import Management, load_config, main
from skillctl import Manager, fingerprint

REPO = Path(__file__).resolve().parents[1]


class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root/'home'
        self.project = self.root/'project with spaces'
        self.project.mkdir()
        self.source = self.root/'source'
        self.skill = self.source/'example-skill'
        self.skill.mkdir(parents=True)
        (self.skill/'SKILL.md').write_text('---\nname: example-skill\ndescription: Test\n---\nKeep my rule.\n', encoding='utf-8')
        self.target = self.project/'.pi/skills'
        self.state = self.root/'receipts'
        self.config = self.home/'.config/skillctl/manager.json'

    def args(self, **values):
        return argparse.Namespace(**(dict(source=self.source, target=['Project Pi='+str(self.target)],
                       state=self.state, project=self.project, dry_run=False, yes=False) | values))

    def run_init(self, args, answers=()):
        output = io.StringIO()
        iterator = iter(answers)
        with redirect_stdout(output), patch('socket.socket', side_effect=AssertionError('No network allowed')):
            code = skill_init.run(args, self.config, home=self.home, ask=lambda _: next(iterator))
        return code, output.getvalue()

    def test_preview_and_cancel_write_nothing(self):
        before = fingerprint(self.root)
        for args, answers in [(self.args(dry_run=True), ()), (self.args(), ('n',))]:
            self.assertEqual(self.run_init(args, answers)[0], 0)
            self.assertEqual(fingerprint(self.root), before)

    def test_explicit_save_is_usable_without_creating_targets_or_state(self):
        before = fingerprint(self.source)
        self.assertEqual(self.run_init(self.args(yes=True))[0], 0)
        config = load_config(self.config)
        app = Management(config)
        self.assertEqual(app.listing()['skills'][0]['status'], 'missing')
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(fingerprint(self.source), before)
        self.assertFalse(app.locations()['service_required'])

    def test_guided_existing_directory_selection_and_receipt_reuse(self):
        manager = Manager(self.source, self.target, self.state)
        manager.install('example-skill')
        before = fingerprint(self.target), fingerprint(self.state)
        rows = skill_init.candidates(self.home, self.project)
        number = next(i for i,r in enumerate(rows, 1) if r['path'] == self.target)
        args = self.args(source=None, target=[], state=None)
        code, output = self.run_init(args, [str(self.source), str(number), str(self.state), 'y'])
        self.assertEqual(code, 0)
        self.assertIn('Preview', output)
        self.assertEqual(load_config(self.config)['state'], self.state)
        self.assertEqual(Management(load_config(self.config)).listing()['skills'][0]['status'], 'current')
        self.assertEqual((fingerprint(self.target), fingerprint(self.state)), before)

    def test_no_existing_locations_can_select_uncreated_project_target(self):
        rows = skill_init.candidates(self.home, self.project)
        self.assertTrue(all(not row['exists'] for row in rows))
        index = next(i for i,r in enumerate(rows,1) if r['path'] == self.target)
        code, output = self.run_init(self.args(target=[]), ['bad', '999', str(index), 'y'])
        self.assertEqual(code, 0)
        self.assertIn('Choose a listed number', output)
        self.assertFalse(self.target.exists())

    def test_custom_target_with_chinese_name_and_spaces(self):
        target = self.project/'自訂 skills'
        self.run_init(self.args(target=[]), ['0', '我的專案', str(target), '是'])
        config = load_config(self.config)
        self.assertEqual(config['targets']['我的專案'], target)
        self.assertFalse(target.exists())

    def test_multiple_targets_have_concrete_next_commands(self):
        other = self.project/'.agents/skills'
        _, output = self.run_init(self.args(yes=True, target=['Pi='+str(self.target), 'Codex='+str(other)]))
        self.assertIn('list --target', output)
        self.assertEqual(set(load_config(self.config)['targets']), {'Pi', 'Codex'})

    def test_existing_config_and_registry_are_never_replaced(self):
        self.run_init(self.args(yes=True))
        before = fingerprint(self.root)
        code, output = self.run_init(self.args(yes=True, source=self.root/'invalid'))
        self.assertEqual(code, 0)
        self.assertIn('kept unchanged', output)
        self.assertEqual(fingerprint(self.root), before)
        self.config.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_init(self.args(yes=True))
        self.assertEqual(self.config.read_text(), '{broken')

    def test_no_tty_and_missing_explicit_choices_fail_closed(self):
        with redirect_stdout(io.StringIO()), patch('sys.stdin.isatty', return_value=False):
            self.assertEqual(main(['--config',str(self.config),'init']), 2)
            self.assertEqual(main(['--config',str(self.config),'init','--yes']), 2)
        self.assertFalse(self.config.exists())

    def test_bad_paths_and_overlaps_do_not_write(self):
        variants = [self.args(source=self.root/'missing', yes=True),
                    self.args(target=['X='+str(self.source)], yes=True),
                    self.args(target=['X='+str(self.state)], yes=True),
                    self.args(target=['X='+str(self.config.parent)], yes=True),
                    self.args(target=['X='+str(self.target),'X='+str(self.project/'another')],yes=True),
                    self.args(target=['X='+str(self.target),'Y='+str(self.target/'nested')],yes=True),
                    self.args(target=['bad'],yes=True), self.args(state=self.source,yes=True)]
        before = fingerprint(self.root)
        for args in variants:
            with self.assertRaises((ValueError,OSError)):
                self.run_init(args)
            self.assertEqual(fingerprint(self.root),before)

    def test_config_created_during_confirmation_is_not_overwritten(self):
        def answer(_):
            self.config.parent.mkdir(parents=True)
            self.config.write_text('created by someone else', encoding='utf-8')
            return 'y'
        with redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
            skill_init.run(self.args(), self.config, home=self.home, ask=answer)
        self.assertEqual(self.config.read_text(), 'created by someone else')

    def test_eof_cancels(self):
        with redirect_stdout(io.StringIO()):
            code = skill_init.run(self.args(), self.config, home=self.home,
                                  ask=lambda _: (_ for _ in ()).throw(EOFError()))
        self.assertEqual(code,130)
        self.assertFalse(self.config.exists())

    def test_linked_target_is_rejected_when_available(self):
        alias = self.root/'alias'
        try:
            alias.symlink_to(self.project, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is not permitted on this host')
        with self.assertRaises(ValueError):
            self.run_init(self.args(target=['X='+str(alias/'.pi/skills')], yes=True))
        self.assertFalse(self.config.exists())

    def test_cli_init_then_locations_and_preview_from_unrelated_cwd(self):
        base = [sys.executable,'-B','-X','utf8',str(REPO/'skill_manage.py'),'--config',str(self.config)]
        commands = [['init','--source',str(self.source),'--target','Project Pi='+str(self.target),
                     '--state',str(self.state),'--yes'], ['locations'],
                    ['preview','install','example-skill','--target','Project Pi']]
        for command in commands:
            completed = subprocess.run(base+command,cwd=self.root,capture_output=True,text=True,
                                       encoding='utf-8',timeout=30)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result['plan_id'])
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
