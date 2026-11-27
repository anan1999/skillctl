"""One-shot skill management for agents. No HTTP server or background process."""
import argparse
import json
import math
from pathlib import Path
import re
import time
import uuid

from skillctl import AGENTS, Manager, fingerprint
from skillctl_content import is_link, valid_name
from skill_quality import assess as assess_quality, parse_json
from skill_usage import assess as assess_usage, collect, write_report
import skill_workflow as workflow
import skill_lock
from skillctl import write_json

DEFAULT_CONFIG = Path.home() / '.config/skillctl/manager.json'
LIMIT = 2 * 1024 * 1024
ACTIONS = ('install', 'update', 'adopt', 'keep', 'merge', 'rollback')


def read_object(path, limit=LIMIT):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('JSON file exceeds the size limit')
    value = parse_json(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def absolute_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Configuration paths must be nonempty strings')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('Configuration paths must be absolute')
    return path.resolve()


def load_config(path):
    config = read_object(path, 65536)
    if type(config.get('schema_version')) is not int or config['schema_version'] != 1:
        raise ValueError('Expected configuration schema_version=1')
    for key in ('source', 'state'):
        config[key] = absolute_path(config.get(key))
    targets = config.get('targets')
    if not isinstance(targets, dict) or not targets:
        raise ValueError('Configure at least one named target')
    if any(not isinstance(label, str) or not label.strip() for label in targets):
        raise ValueError('Target labels must be nonempty strings')
    # Keep the original target path until Manager validates symlinks.
    for value in targets.values():
        absolute_path(value)
    config['targets'] = {label: Path(value).expanduser() for label, value in targets.items()}
    for key in ('repo', 'codex_home', 'usage_report', 'quality_report'):
        if config.get(key) is not None:
            config[key] = absolute_path(config[key])
    return config


def harness(path):
    text = str(path).replace('\\', '/').rstrip('/').lower()
    if text.endswith('/.codex/skills'):
        return 'codex'
    for name, entry in AGENTS.items():
        if any(text.endswith('/' + entry[scope]) for scope in ('user', 'project')):
            return name
    return None


class Management:
    def __init__(self, config):
        self.config = config

    def target(self, label=None):
        if label is None and len(self.config['targets']) == 1:
            label = next(iter(self.config['targets']))
        if label not in self.config['targets']:
            raise ValueError('Choose a configured target label; use locations to list them')
        return label, self.config['targets'][label]

    def manager(self, label=None):
        label, path = self.target(label)
        return label, Manager(self.config['source'], path, self.config['state'])

    def locations(self):
        return {'source': str(self.config['source']), 'state': str(self.config['state']),
                'targets': [{'label': label, 'path': str(path), 'harness': harness(path)}
                            for label, path in self.config['targets'].items()],
                'reports': {key: str(self.config[key]) if self.config.get(key) else None
                            for key in ('usage_report', 'quality_report')},
                'network': False, 'service_required': False}

    def listing(self, label=None, query=''):
        label, manager = self.manager(label)
        rows = manager.scan()
        found = {row['name'] for row in rows if Path(row['path']).parent == manager.target}
        for name in manager.names:
            if name not in found:
                try:
                    rows.append({**manager.inspect(name, manager.db()), 'path': str(manager.target/name)})
                except (ValueError, OSError) as error:
                    rows.append({'name': name, 'status': 'invalid', 'error': str(error)})
        for row in rows:
            row['description'] = ''
            if row['status'] not in ('invalid', 'linked'):
                folder = Path(row['path'])
                if not (folder/'SKILL.md').is_file():
                    folder = manager.sources.get(row['name'], folder)
                try:
                    with (folder/'SKILL.md').open('r', encoding='utf-8-sig') as stream:
                        match = re.search(r'^description:\s*(.+)$', stream.read(65536), re.M)
                    row['description'] = match[1] if match else ''
                except (ValueError, OSError):
                    pass
        query = query.casefold()
        fields = ('name', 'status', 'description', 'path', 'source', 'adoptable', 'error', 'advice')
        return {'target': label, 'path': str(manager.target),
                'skills': [{key: row[key] for key in fields if key in row}
                           for row in rows if query in (row['name']+' '+row['description']).casefold()],
                'source_scope': 'Configured local source only; no remote version check was performed.'}

    def usage(self, name=None, days=7, label=None, refresh=False):
        if name is not None and not valid_name(name):
            raise ValueError('Invalid skill name')
        agent = harness(self.target(label)[1]) if label is not None else 'codex'
        path = self.config.get('usage_report')
        if agent != 'codex':
            return {'skills': {name or '*': assess_usage(None, name or '*', days, harness=agent)}}
        if refresh:
            if path is None or self.config.get('codex_home') is None:
                raise ValueError('Refresh requires configured codex_home and usage_report')
            report = collect(self.config['codex_home'])
            write_report(report, path)
        else:
            report = read_object(path) if path is not None else None
        probe = assess_usage(report, name or '*', days)
        if probe['status'] != 'ready':
            return {'skills': {name or '*': probe}}
        names = [name] if name else sorted(report['skills'])
        # The probe validated the entire report. Keep repeated metadata outside rows.
        results = {item: {'status': 'ready', **report['skills'].get(item, {}).get(str(days),
                          {'reads': 0, 'conversations': 0, 'last_read': None})} for item in names}
        return {'days': days, 'refreshed': refresh, 'scope': probe['scope'],
                'window_end': probe['window_end'], 'coverage': probe['coverage'],
                'partial': probe['partial'], 'stale': probe['stale'], 'is_fixture': probe['is_fixture'],
                'skills': results, 'note': 'Lower bound of confirmed SKILL.md reads; not completed tasks or usage of a specific installed copy.'}

    def quality(self, name=None, label=None):
        label, manager = self.manager(label)
        if name is not None and not valid_name(name):
            raise ValueError('Invalid skill name')
        path = self.config.get('quality_report')
        report = read_object(path) if path is not None else None
        names = [name] if name else sorted(set(manager.names) | {
            row['name'] for row in manager.scan() if Path(row['path']).parent == manager.target})
        results = {}
        for item in names:
            try:
                results[item] = assess_quality(report, item, fingerprint(manager.target/item))
            except (ValueError, OSError) as error:
                results[item] = {'status': 'error', 'error': str(error)}
        return {'target': label, 'path': str(manager.target), 'skills': results,
                'note': 'Existing text-simulation quality reports checked against current file hashes; no models, skill scripts or devices were run.'}

    def doctor(self, name=None, label=None):
        _, manager = self.manager(label)
        if name is not None and not valid_name(name):
            raise ValueError('Invalid skill name')
        names = [name] if name else sorted(set(manager.names) | {
            row['name'] for row in manager.scan() if Path(row['path']).parent == manager.target})
        return manager.doctor(list(self.config['targets'].values()), names=names)

    @staticmethod
    def operation(manager, action, name, apply=False, expected=None):
        if action == 'update':
            if name not in manager.db()['installed'] or fingerprint(manager.target/name) is None:
                raise ValueError('Update requires an installed managed skill; preview install or adopt first')
            action = 'install'
        if action == 'install':
            return manager.install(name, apply=apply, expected=expected)
        if action == 'adopt':
            return manager.adopt(name, apply=apply, expected=expected)
        if action == 'rollback':
            return manager.rollback(name, apply=apply, expected=expected)
        return manager.reconcile(name, action, apply=apply, expected=expected)

    @staticmethod
    def binding(manager):
        return {key: str(getattr(manager, key)) for key in ('source', 'target', 'state')}

    def preview(self, action, name, label=None):
        if action not in ACTIONS or not valid_name(name):
            raise ValueError('Invalid action or skill name')
        label, manager = self.manager(label)
        stamp = manager.preview_stamp(name, action)
        comparison = manager.diff(name, True) if name in manager.sources and action != 'rollback' else None
        try:
            result = self.operation(manager, action, name)
        except (ValueError, OSError) as error:
            return {'ok': False, 'target': label, 'path': str(manager.target), 'action': action,
                    'name': name, 'plan_id': None, 'error': str(error), 'comparison': comparison}
        manager.verify_preview(name, stamp, action)
        plan_id = None
        if result.get('ok', True) and result.get('action') != 'unchanged':
            plan_id = uuid.uuid4().hex
            plans = manager.state/'plans'
            if is_link(plans):
                raise ValueError('Linked plan storage is not supported')
            plans.mkdir(parents=True, exist_ok=True)
            record = dict(schema_version=1, action=action, name=name, target_label=label,
                          binding=self.binding(manager), stamp=stamp, expires_at=time.time()+600)
            with (plans/(plan_id+'.json')).open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False)
        return {'ok': result.get('ok', True), 'target': label, 'path': str(manager.target),
                'action': action, 'name': name, 'plan_id': plan_id,
                'expires_in_seconds': 600 if plan_id else 0, 'result': result, 'comparison': comparison,
                'note': 'Preview saves only a local plan, without changing skills. Applying requires user authorization; changed files or receipts block it.'}

    def apply(self, plan_id, label=None):
        if not isinstance(plan_id, str) or not re.fullmatch('[a-f0-9]{32}', plan_id):
            raise ValueError('Invalid plan ID')
        label, manager = self.manager(label)
        plans = manager.state/'plans'
        path = plans/(plan_id+'.json')
        if is_link(plans) or is_link(path):
            raise ValueError('Linked plans are not supported')
        record = read_object(path, 65536)
        if (type(record.get('schema_version')) is not int or record['schema_version'] != 1
                or record.get('binding') != self.binding(manager) or record.get('target_label') != label
                or record.get('action') not in ACTIONS or not valid_name(record.get('name', ''))
                or not isinstance(record.get('stamp'), str) or not re.fullmatch('[a-f0-9]{64}', record['stamp'])):
            raise ValueError('Plan does not match this configuration')
        expiry = record.get('expires_at')
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or not time.time() < expiry <= time.time()+601:
            raise ValueError('Preview expired; preview again')
        # Atomic claim: separate CLI processes cannot apply the same preview twice.
        claimed = plans/(plan_id+'.claimed')
        path.rename(claimed)
        try:
            result = self.operation(manager, record['action'], record['name'], True, record['stamp'])
            return {'ok': result.get('ok', True), 'target': label, 'name': record['name'], 'result': result}
        finally:
            claimed.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest='command', required=True)
    import skill_init
    skill_init.add_parser(sub)
    import skill_task
    skill_task.add_parser(sub)
    sub.add_parser('locations')
    locked = sub.add_parser('lock', help='Create, inspect and restore a portable project lockfile')
    lock_sub = locked.add_subparsers(dest='lock_action', required=True)
    lock_create = lock_sub.add_parser('create', help='Preview a lockfile of managed installed skills, including local edits')
    lock_create.add_argument('names', nargs='*')
    lock_restore = lock_sub.add_parser('restore', help='Preview restoring exact locked contents')
    lock_restore.add_argument('--keep-local', action='store_true', help='Skip local conflicts without changing their bytes or receipts')
    lock_check = lock_sub.add_parser('check', help='Compare installed file hashes with a lockfile')
    lock_inspect = lock_sub.add_parser('inspect', help='Inspect pins without a configuration or network')
    for command in (lock_create, lock_restore, lock_check, lock_inspect):
        command.add_argument('--file', type=Path, default=Path('skillctl.lock.json'))
    lock_apply = lock_sub.add_parser('apply', help='Apply an unexpired lock preview to its bound target')
    lock_apply.add_argument('plan_id')
    for command in (lock_create, lock_restore, lock_check, lock_apply):
        command.add_argument('--target', help='Configured target label')
    listing = sub.add_parser('list')
    listing.add_argument('--query', default='')
    usage = sub.add_parser('usage')
    usage.add_argument('name', nargs='?')
    usage.add_argument('--days', type=int, choices=(7, 30), default=7)
    usage.add_argument('--refresh', action='store_true')
    quality = sub.add_parser('quality'); quality.add_argument('name', nargs='?')
    doctor = sub.add_parser('doctor'); doctor.add_argument('name', nargs='?')
    impact = sub.add_parser('impact'); impact.add_argument('name')
    learn = sub.add_parser('learn')
    learn_sub = learn.add_subparsers(dest='learn_action', required=True)
    record = learn_sub.add_parser('record'); record.add_argument('--input', type=Path, required=True)
    records = learn_sub.add_parser('list')
    show = learn_sub.add_parser('show'); show.add_argument('receipt_id')
    compare = sub.add_parser('compare')
    compare_sub = compare.add_subparsers(dest='compare_action', required=True)
    prepare = compare_sub.add_parser('prepare')
    for option in ('suite', 'old', 'new', 'output'):
        prepare.add_argument('--'+option, type=Path, required=True)
    prepare.add_argument('--model', required=True)
    prepare.add_argument('--max-calls', type=int, default=8)
    prepare.add_argument('--max-tokens', type=int, default=1024)
    prepare.add_argument('--timeout', type=int, default=60)
    prepare.add_argument('--total-timeout', type=int, default=300)
    run = compare_sub.add_parser('run')
    for option in ('plan', 'adapter', 'output'):
        run.add_argument('--'+option, type=Path, required=True)
    preview = sub.add_parser('preview')
    preview.add_argument('action', choices=ACTIONS); preview.add_argument('name')
    apply = sub.add_parser('apply'); apply.add_argument('plan_id')
    for command in (listing, usage, quality, doctor, preview, apply, impact, record, records, show):
        command.add_argument('--target', help='Configured target label, not an arbitrary path')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            return skill_init.run(args, args.config)
        if args.command == 'lock' and args.lock_action == 'inspect':
            value, _, sha = skill_lock.load(args.file)
            print(json.dumps({'ok': True, 'sha256': sha, 'skills': skill_lock.summary(value),
                              'note': 'Portable content snapshot, not a signature or behavior certificate.'}, ensure_ascii=False, indent=2))
            return 0
        app = Management(load_config(args.config))
        if args.command == 'locations':
            result = app.locations()
        elif args.command == 'task':
            result = skill_task.dispatch(app,args)
        elif args.command == 'list':
            result = app.listing(args.target, args.query)
        elif args.command == 'usage':
            result = app.usage(args.name, args.days, args.target, args.refresh)
        elif args.command in ('quality', 'doctor'):
            result = getattr(app, args.command)(args.name, args.target)
        elif args.command == 'impact':
            _, manager = app.manager(args.target)
            result = workflow.impact(manager, args.name)
        elif args.command == 'lock':
            _, manager = app.manager(args.target)
            if args.lock_action == 'create':
                result = skill_lock.create_preview(manager, args.file, args.names)
            elif args.lock_action == 'restore':
                result = skill_lock.restore_preview(manager, args.file, args.keep_local)
            elif args.lock_action == 'check':
                result = skill_lock.check(manager, args.file)
            else:
                result = skill_lock.apply(manager, args.plan_id)
        elif args.command == 'learn':
            _, manager = app.manager(args.target)
            result = (workflow.record_learning(manager, read_object(args.input)) if args.learn_action == 'record'
                      else workflow.learning_receipts(manager, getattr(args, 'receipt_id', None)))
        elif args.command == 'compare':
            if args.compare_action == 'prepare':
                plan = workflow.prepare_comparison(read_object(args.suite), args.old, args.new, args.model,
                    args.max_calls, args.max_tokens, args.timeout, args.total_timeout)
                folder = workflow.fresh_directory(args.output)
                write_json(folder/'plan.json', plan)
                result = {'ok': True, 'plan': str(folder/'plan.json'), 'planned_calls': plan['planned_calls'],
                          'limits': plan['limits'], 'max_requested_output_tokens': plan['max_requested_output_tokens'],
                          'network': False, 'note': plan['note']}
            else:
                result = workflow.run_comparison(read_object(args.plan), read_object(args.adapter), args.output)
        elif args.command == 'preview':
            result = app.preview(args.action, args.name, args.target)
        else:
            result = app.apply(args.plan_id, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('ok') is False else 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as error:
        print(json.dumps({'ok': False, 'status': 'error', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
