"""Exercise the actual CLI with isolated user/project/state directories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from skillctl import fingerprint


ROOT = Path(__file__).resolve().parents[1]


class HarnessCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        (self.project / '.git').mkdir()  # discovery boundary
        self.home = self.root / 'home'
        self.home.mkdir()
        self.source = self.root / 'source'
        # These scenarios install one skill and then diagnose the entire fixture.
        # Keep the fixture independent of new packages added to the public catalog.
        shutil.copytree(ROOT / 'skills/example-skill', self.source / 'skills/example-skill')
        (self.source / 'catalog.json').write_text(
            json.dumps({'schema': 1, 'skills': ['example-skill']}), encoding='utf-8')
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0')

    def cli(self, *args, code=0):
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'skillctl.py'),
             '--source', str(self.source), '--state', str(self.root / 'state'), *args],
            cwd=self.project, env=self.env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout or result.stderr)

    def test_eight_target_combinations_and_rollback(self):
        paths = {
            'pi': ('.pi/skills', '.pi/agent/skills', '/skill:example-skill'),
            'codex': ('.agents/skills', '.agents/skills', '$example-skill'),
            'claude-code': ('.claude/skills', '.claude/skills', '/example-skill'),
            'cursor': ('.cursor/skills', '.cursor/skills', '/example-skill'),
        }
        for agent, (project_path, user_path, invoke) in paths.items():
            for scope, base, suffix in [('project', self.project, project_path), ('user', self.home, user_path)]:
                with self.subTest(agent=agent, scope=scope):
                    target = base / suffix
                    flags = ['--agent', agent, '--scope', scope]
                    preview = self.cli(*flags, 'install', 'example-skill', '--dry-run')
                    self.assertEqual(str(target), preview['target'])
                    self.assertFalse(target.exists())
                    result = self.cli('install', 'example-skill', *flags)
                    self.assertEqual([invoke], result['invoke'])
                    self.assertEqual(fingerprint(self.source / 'skills/example-skill'),
                                     fingerprint(target / 'example-skill'))
                    doctor = self.cli(*flags, 'doctor')
                    self.assertTrue(doctor['ok'])
                    self.assertEqual('current', doctor['skills'][0]['status'])
                    self.cli('rollback', 'example-skill', *flags)
                    self.assertFalse((target / 'example-skill').exists())

    def test_drift_preview_blocks_and_preserves_local_edit(self):
        flags = ['--agent', 'pi']
        self.cli(*flags, 'install', 'example-skill')
        installed = self.project / '.pi/skills/example-skill/SKILL.md'
        installed.write_text('local edits', encoding='utf-8')
        doctor = self.cli('doctor', *flags, code=1)
        self.assertEqual('local-drift', doctor['skills'][0]['status'])
        self.assertTrue(self.cli('install', 'example-skill', *flags, '--dry-run', code=1)['skills'][0]['blocked'])
        self.cli('install', 'example-skill', *flags, code=2)
        self.assertEqual('local edits', installed.read_text())

    def test_doctor_missing_is_read_only_and_finds_same_name_copy(self):
        other = self.project / '.agents/skills/example-skill'
        shutil.copytree(self.source / 'skills/example-skill', other)
        before = fingerprint(self.root)
        doctor = self.cli('doctor', '--agent', 'pi', code=1)
        self.assertEqual('missing', doctor['skills'][0]['status'])
        self.assertEqual(str(other), doctor['skills'][0]['copies'][0]['path'])
        self.assertEqual(before, fingerprint(self.root))
        self.cli('install', 'example-skill', '--agent', 'pi')
        doctor = self.cli('doctor', '--agent', 'pi')
        self.assertTrue(doctor['skills'][0]['duplicate_content_equal'])
        (other / 'SKILL.md').write_text('different', encoding='utf-8')
        doctor = self.cli('doctor', '--agent', 'pi')
        self.assertFalse(doctor['skills'][0]['duplicate_content_equal'])

    def test_invalid_source_does_not_hide_other_skills(self):
        folder = self.source / 'skills/broken'
        folder.mkdir()
        (folder / 'SKILL.md').write_text('invalid', encoding='utf-8')
        (self.source / 'catalog.json').write_text(
            json.dumps({'schema': 1, 'skills': ['broken', 'example-skill']}), encoding='utf-8')
        doctor = self.cli('doctor', '--agent', 'pi', code=1)
        self.assertEqual(['invalid', 'missing'], [row['status'] for row in doctor['skills']])

    def test_legacy_target_and_argument_validation(self):
        custom = self.root / 'custom'
        result = self.cli('--target', str(custom), 'install', 'example-skill')
        self.assertIsInstance(result, list)
        self.assertTrue(self.cli('--target', str(custom), 'check')['ok'])
        self.cli('--target', str(custom), 'doctor', '--agent', 'pi', code=2)
        self.cli('doctor', '--scope', 'user', code=2)
        self.cli('doctor', '--agent', 'pi', '--scope', 'user', '--project', str(self.project), code=2)
        self.cli('doctor', '--agent', 'pi', '--project', str(self.root / 'absent'), code=2)
        self.assertEqual(str(self.home / '.codex/skills'), self.cli('doctor', code=1)['target'])
        result = self.cli('install', 'example-skill', '--agent', 'pi', '--project', str(self.home))
        self.assertEqual(str(self.home / '.pi/skills'), result['target'])

    def test_deleted_managed_skill_is_drift_and_reinstall_is_blocked(self):
        self.cli('install', 'example-skill', '--agent', 'pi')
        shutil.rmtree(self.project / '.pi/skills/example-skill')
        self.assertEqual('local-drift', self.cli('doctor', '--agent', 'pi', code=1)['skills'][0]['status'])
        self.assertTrue(self.cli('install', 'example-skill', '--agent', 'pi', '--dry-run', code=1)['skills'][0]['blocked'])

    def test_scan_adopt_keep_and_merge_cli(self):
        for agent, folder in [('pi', '.pi'), ('codex', '.agents'), ('claude-code', '.claude'), ('cursor', '.cursor')]:
            with self.subTest(agent=agent):
                target = self.project / folder / 'skills/example-skill'
                shutil.copytree(self.source / 'skills/example-skill', target)
                (target / 'local.txt').write_text('my local change', encoding='utf-8')
                flags = ['--agent', agent]
                self.assertEqual('unmanaged-conflict', self.cli('scan', *flags)['skills'][0]['status'])
                self.assertEqual('would-adopt', self.cli('adopt', 'example-skill', *flags)[0]['action'])
                self.cli('adopt', 'example-skill', '--apply', *flags)
                diff = self.cli('diff', 'example-skill', '--json', *flags)[0]
                self.assertEqual('customized', diff['status'])
                self.assertTrue(diff['blocked_reason'])
                self.cli('reconcile', 'example-skill', '--strategy', 'keep', '--apply', *flags)
                (self.source / 'skills/example-skill/upstream.txt').write_text(agent, encoding='utf-8')
                self.cli('reconcile', 'example-skill', '--strategy', 'merge', '--apply', *flags)
                self.assertEqual('my local change', (target / 'local.txt').read_text())
                self.assertEqual(agent, (target / 'upstream.txt').read_text())
                self.cli('install', 'example-skill', '--dry-run', *flags, code=1)
                self.cli('rollback', 'example-skill', *flags)

    def test_cli_conflict_exit_code_and_scan_all_read_only(self):
        flags = ['--agent', 'cursor']
        self.cli('install', 'example-skill', *flags)
        local = self.project / '.cursor/skills/example-skill/SKILL.md'
        upstream = self.source / 'skills/example-skill/SKILL.md'
        local.write_text(local.read_text() + 'LOCAL\n', encoding='utf-8')
        upstream.write_text(upstream.read_text() + 'UPSTREAM\n', encoding='utf-8')
        before = fingerprint(self.root)
        result = self.cli('reconcile', 'example-skill', '--strategy', 'merge', *flags, code=1)
        self.assertFalse(result['ok'])
        self.cli('scan', '--all-agents')
        self.assertFalse(self.cli('scan', '--all-agents', '--scope', 'user')['skills'])
        self.assertEqual(1, len(self.cli('scan', '--all-agents', '--scope', 'project')['skills']))
        self.assertEqual(before, fingerprint(self.root))


if __name__ == '__main__':
    unittest.main()
