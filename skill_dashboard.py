"""Serve the local skillctl management page; Python standard library only."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from skillctl import AGENTS, Manager, fingerprint
from skillctl_content import valid_name


ROOT = Path(__file__).resolve().parent


class Dashboard:
    def __init__(self, source, targets, state):
        self.source = Path(source).resolve()
        self.targets = {str(i): {'label': label, 'path': str(Path(path).expanduser().absolute())}
                        for i, (label, path) in enumerate(targets)}
        self.state = Path(state)
        self.secret = secrets.token_urlsafe(32)
        self.plans = {}
        self.lock = threading.RLock()
        for target in self.targets:
            self.manager(target)  # validate configuration before opening a port

    def manager(self, target):
        if target not in self.targets:
            raise ValueError('Unknown target; use a configured target from the page.')
        return Manager(self.source, self.targets[target]['path'], self.state)

    def configuration(self):
        # No filesystem scan: refresh can show the configured scope immediately.
        return {'source': str(self.source), 'targets': [
            {'id': target_id, **config} for target_id, config in self.targets.items()]}

    def inventory(self, target=None):
        if target is not None and target not in self.targets:
            raise ValueError('Unknown target; use a configured target from the page.')
        targets = []
        for target_id, config in self.targets.items():
            if target is not None and target != target_id:
                continue
            try:
                m = self.manager(target_id)
                rows = m.scan()
                db = m.db()
                found = {row['name'] for row in rows if Path(row['path']).parent == m.target}
                for name in m.names:
                    if name not in found:
                        try:
                            rows.append({**m.inspect(name, db), 'path': str(m.target / name), 'adoptable': False})
                        except (ValueError, OSError) as error:
                            rows.append({'name': name, 'status': 'invalid', 'error': str(error), 'adoptable': False})
                for row in rows:
                    folder = Path(row.get('path', ''))
                    row['description'] = ''
                    if row['status'] not in ('invalid', 'linked') and (folder / 'SKILL.md').is_file():
                        # Small description excerpt for local search, never rendered as HTML.
                        import re
                        text = (folder / 'SKILL.md').read_text(encoding='utf-8-sig')
                        match = re.search(r'^description:\s*(.+)$', text, re.M)
                        row['description'] = match[1] if match else ''
                    row['present'] = row['status'] != 'linked' and (folder / 'SKILL.md').is_file()
                    row['managed'] = row['name'] in db['installed'] and folder.parent == m.target
                    row['has_source'] = row['name'] in m.sources
                installed = sum(row['present'] for row in rows)
                managed = sum(row['present'] and row['managed'] for row in rows)
                counts = {'installed': installed, 'managed': managed, 'unmanaged': installed - managed,
                          'missing': sum(not row['present'] and row['has_source'] and row['status'] != 'linked' for row in rows),
                          'attention': sum(row['status'] in ('invalid', 'linked', 'nested') for row in rows)}
                targets.append({'id': target_id, **config, 'skills': rows, 'counts': counts})
            except (ValueError, OSError) as error:
                targets.append({'id': target_id, **config, 'skills': [], 'error': str(error)})
        return {'source': str(self.source), 'targets': targets}

    def detail(self, target, name):
        if not valid_name(name):
            raise ValueError('Invalid skill name')
        m = self.manager(target)
        result = {'name': name, 'local_files': fingerprint(m.target / name),
                  'upstream_files': m.validate(name) if name in m.sources else None,
                  'receipt': m.db()['installed'].get(name)}
        result['comparison'] = m.diff(name, True) if name in m.sources else None
        return result

    def preview(self, target, name, action):
        if action not in ('adopt', 'keep', 'merge') or not valid_name(name):
            raise ValueError('Invalid action or skill name')
        with self.lock:
            m = self.manager(target)
            stamp = m.preview_stamp(name)
            result = m.adopt(name) if action == 'adopt' else m.reconcile(name, action)
            m.verify_preview(name, stamp)
            now = time.monotonic()
            self.plans = {key: plan for key, plan in self.plans.items() if plan['expires'] > now}
            if len(self.plans) >= 128:
                self.plans.pop(next(iter(self.plans)))
            plan_id = None
            if result.get('ok', True):
                plan_id = secrets.token_urlsafe(24)
                self.plans[plan_id] = {'target': target, 'name': name, 'action': action,
                                       'stamp': stamp, 'expires': now + 600}
            return {'plan_id': plan_id, 'expires_in_seconds': 600 if plan_id else 0,
                    'target': self.targets[target], 'name': name, 'action': action, 'result': result,
                    'comparison': m.diff(name, True) if name in m.sources else None}

    def apply(self, plan_id):
        with self.lock:
            plan = self.plans.pop(plan_id, None)
            if not plan or plan['expires'] <= time.monotonic():
                raise ValueError('Preview expired or was already used. Preview again before applying.')
            m = self.manager(plan['target'])
            if plan['action'] == 'adopt':
                result = m.adopt(plan['name'], apply=True, expected=plan['stamp'])
            else:
                result = m.reconcile(plan['name'], plan['action'], apply=True, expected=plan['stamp'])
            if result.get('ok') is False:
                raise ValueError('Reconciliation blocked; preview again to inspect conflicts.')
            return {'ok': True, 'result': result}


def make_server(app, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # do not log skill paths or capability headers

        def reply(self, code, value, content_type='application/json; charset=utf-8'):
            data = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, api=False):
            expected_host = '127.0.0.1:' + str(self.server.server_port)
            origin = self.headers.get('Origin')
            if self.headers.get('Host') != expected_host or (origin is not None and origin != 'http://' + expected_host):
                self.reply(403, {'error': 'Only this local dashboard origin is allowed.'})
                return False
            if api and not hmac.compare_digest(self.headers.get('X-Skillctl-Token', ''), app.secret):
                self.reply(403, {'error': 'Reload the local dashboard before using its API.'})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path)
            if not self.authorized(path.path.startswith('/api/')):
                return
            try:
                if path.path in ('/', '/dashboard.js', '/dashboard.css'):
                    filename, mime = {'/': ('skill_dashboard.html', 'text/html'),
                                      '/dashboard.js': ('skill_dashboard.js', 'text/javascript'),
                                      '/dashboard.css': ('skill_dashboard.css', 'text/css')}[path.path]
                    data = (ROOT / filename).read_bytes()
                    if path.path == '/':
                        data = data.replace(b'__SESSION_TOKEN__', app.secret.encode())
                    self.reply(200, data, mime + '; charset=utf-8')
                elif path.path == '/api/config':
                    self.reply(200, app.configuration())
                elif path.path == '/api/inventory':
                    query = parse_qs(path.query)
                    with app.lock:
                        self.reply(200, app.inventory(query.get('target', [None])[0]))
                elif path.path == '/api/detail':
                    query = parse_qs(path.query)
                    with app.lock:
                        self.reply(200, app.detail(query['target'][0], query['name'][0]))
                else:
                    self.reply(404, {'error': 'Not found'})
            except (ValueError, OSError, KeyError) as error:
                self.reply(400, {'error': str(error)})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    raise ValueError('Expected a small JSON request.')
                # Drain bounded request bodies before rejecting credentials. Closing
                # with unread bytes can reset the connection before Windows clients
                # receive the explanatory 403 response.
                self.connection.settimeout(10)
                payload = self.rfile.read(size)
                if not self.authorized(True):
                    return
                if self.headers.get('Content-Type') != 'application/json' or len(payload) != size:
                    raise ValueError('Expected a complete JSON request.')
                body = json.loads(payload)
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/preview':
                    if set(body) != {'target', 'name', 'action'} or not all(isinstance(v, str) for v in body.values()):
                        raise ValueError('Expected target, name and action strings.')
                    result = app.preview(**body)
                elif self.path == '/api/apply':
                    if set(body) != {'plan_id'} or not isinstance(body['plan_id'], str):
                        raise ValueError('Expected a preview ID.')
                    result = app.apply(body['plan_id'])
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
                self.reply(200, result)
            except (ValueError, OSError, KeyError) as error:
                self.reply(409, {'error': str(error)})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--state', type=Path, default=Path.home() / '.codex/skill-manager-state')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--agent', choices=AGENTS, action='append')
    parser.add_argument('--target', action='append', help='Named custom target: LABEL=PATH; repeat for multiple directories')
    parser.add_argument('--port', type=int, default=0, help='Loopback port (default: choose a free port)')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    if args.target and args.agent:
        parser.error('Choose --target or --agent')
    if not args.project.is_dir():
        parser.error('Project directory does not exist')
    if args.target:
        targets = [entry.split('=', 1) for entry in args.target]
        if any(len(entry) != 2 or not all(entry) for entry in targets):
            parser.error('Each --target must be LABEL=PATH')
    else:
        targets = [(agent, args.project / AGENTS[agent]['project']) for agent in (args.agent or AGENTS)]
    server = make_server(Dashboard(args.source, targets, args.state), args.port)
    url = 'http://127.0.0.1:' + str(server.server_port)
    print(url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
