"""Read-only task dashboard, served on loopback for the Codex browser panel."""
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from http.cookies import SimpleCookie
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request, urlopen
import fcntl
import jobs
from notifier import bridge


class ChatCatalog:
    def __init__(self, thread_id):
        self.thread_id = thread_id
        self.records = []
        self.error = None
        self.checked = 0
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            if time.monotonic() - self.checked > 30:
                try:
                    reply = bridge({'action': 'list_threads', 'thread_id': self.thread_id})
                    text = next(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
                    data = json.loads(text)
                    self.records = [{key: item.get(key) for key in ['id', 'title', 'kind', 'status', 'cwd']}
                                    for item in data.get('pinnedThreads', []) + data.get('threads', [])]
                    self.error = None
                except Exception as error:
                    self.error = str(error)
                self.checked = time.monotonic()
            tasks = snapshot(self.thread_id, 'all')['tasks']
            counts = {}
            for task in tasks:
                count = counts.setdefault(task['thread_id'], {'total': 0, 'active': 0})
                count['total'] += 1
                count['active'] += task['status'] in ['starting', 'running']
            by_id = {item['id']: dict(item) for item in self.records if isinstance(item.get('id'), str)}
            for thread_id in set(counts) | {self.thread_id}:
                if thread_id not in by_id:
                    by_id[thread_id] = {'id': thread_id, 'title': None, 'kind': 'codex', 'status': 'unknown'}
            for item in by_id.values():
                item.update(counts.get(item['id'], {'total': 0, 'active': 0}))
                item['current'] = item['id'] == self.thread_id
            return {'chats': list(by_id.values()), 'error': self.error,
                    'note': '显示全部置顶及最近最多 50 个未归档聊天，并补充有后台任务记录的聊天。'}


def snapshot(thread_id, scope='chat'):
    tasks = []
    unreadable = 0
    for file in jobs.root().glob('cli-*/result.json'):
        try:
            job = json.loads(file.read_text())
            if not isinstance(job, dict) or not all(key in job for key in ['task_id', 'thread_id', 'status', 'command']):
                raise ValueError('Invalid task record')
            if scope == 'chat' and job['thread_id'] != thread_id:
                continue
            item = {key: job.get(key) for key in ['task_id', 'thread_id', 'status', 'command', 'cwd',
                'created_at', 'started_at', 'finished_at', 'duration_seconds', 'exit_code',
                'output_tail', 'output_truncated', 'notification_status', 'notification_error', 'error']}
            if job['status'] in ['starting', 'running']:
                try:
                    # Resolve from validated task_id rather than trusting an arbitrary output_path.
                    with (jobs.task_folder(job['task_id']) / 'output.log').open('rb') as output:
                        output.seek(0, 2)
                        size = output.tell()
                        item['output_truncated'] = item.get('output_truncated', False) or size >= jobs.OUTPUT_LIMIT
                        output.seek(max(0, size - 32000))
                        item['output_tail'] = output.read().decode('utf-8', errors='replace')[-8000:]
                except (OSError, ValueError):
                    pass
            tasks.append(item)
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    tasks.sort(key=lambda task: task.get('created_at') or '', reverse=True)
    counts = {'total': len(tasks), 'active': 0, 'completed': 0, 'failed': 0, 'cancelled': 0}
    for task in tasks:
        if task['status'] in ['starting', 'running']:
            counts['active'] += 1
        elif task['status'] == 'completed':
            counts['completed'] += 1
        elif task['status'] in ['failed', 'timed_out']:
            counts['failed'] += 1
        elif task['status'] == 'cancelled':
            counts['cancelled'] += 1
    return {'thread_id': thread_id, 'scope': scope, 'counts': counts, 'tasks': tasks,
            'unreadable_records': unreadable, 'updated_at': datetime.now(timezone.utc).isoformat()}


def handler(thread_id, token):
    catalog = ChatCatalog(thread_id)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, content_type='application/json; charset=utf-8'):
            encoded = data.encode() if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if status == 200 and content_type.startswith('text/html'):
                self.send_header('Set-Cookie', f'bt_panel_{self.server.server_port}={token}; Path=/; HttpOnly; SameSite=Strict')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in allowed or (origin and origin not in {'http://' + h for h in allowed}):
                return self.reply(403, {'error': 'Local origin required'})
            path = urlsplit(self.path)
            if path.path == '/':
                return self.reply(200, Path(__file__).with_name('panel.html').read_text(), 'text/html; charset=utf-8')
            if path.path not in ['/api/tasks', '/api/chats']:
                return self.reply(404, {'error': 'Not found'})
            provided = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not provided:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get('Cookie', ''))
                    entry = cookie.get(f'bt_panel_{self.server.server_port}')
                    provided = entry.value if entry else ''
                except Exception:
                    provided = ''
            if not secrets.compare_digest(provided.encode(), token.encode()):
                return self.reply(401, {'error': 'Panel token required'})
            if path.path == '/api/chats':
                return self.reply(200, catalog.read())
            query = parse_qs(path.query)
            scope = query.get('scope', ['chat'])[0]
            if scope not in ['chat', 'all', 'thread']:
                return self.reply(400, {'error': 'Unknown scope'})
            selected = thread_id
            if scope == 'thread':
                selected = query.get('thread_id', [''])[0]
                if selected not in {item['id'] for item in catalog.read()['chats']}:
                    return self.reply(404, {'error': 'Unknown chat'})
            return self.reply(200, snapshot(selected, 'all' if scope == 'all' else 'chat'))
    return Handler


def serve(thread_id, registry):
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(thread_id, token))
    port = server.server_port
    data = {'pid': os.getpid(), 'thread_id': thread_id, 'url': f'http://127.0.0.1:{port}/',
            'api_url': f'http://127.0.0.1:{port}/api/tasks', 'token': token, 'version': '0.1.9'}
    jobs.save(registry.parent, data)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def valid_registry(file, thread_id):
    try:
        record = json.loads(file.read_text())
        if record['thread_id'] != thread_id or record.get('version') != '0.1.9':
            return None
        url = urlsplit(record['api_url'])
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path != '/api/tasks':
            return None
        with urlopen(Request(record['api_url'], headers={'Authorization': 'Bearer ' + record['token']}), timeout=1) as response:
            payload = json.load(response)
        return record if payload['thread_id'] == thread_id else None
    except (OSError, ValueError, KeyError):
        return None


def open_panel(thread_id):
    if not thread_id:
        raise ValueError('Originating chat metadata unavailable')
    directory = jobs.root().parent / 'panels' / hashlib.sha256(thread_id.encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry = directory / 'result.json'
    with (directory / 'launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = valid_registry(registry, thread_id)
        if record is None:
            with (directory / 'panel.log').open('ab') as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve', thread_id, str(registry)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                threading.Thread(target=process.wait, daemon=True).start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                record = valid_registry(registry, thread_id)
                if record:
                    break
                if process.poll() is not None:
                    raise RuntimeError('Task panel failed to start; inspect ' + str(directory / 'panel.log'))
                time.sleep(0.05)
            if not record:
                raise RuntimeError('Task panel did not start within 5 seconds')
    return {'url': record['url'] + '?' + urlencode({'session_id': thread_id}), 'counts': snapshot(thread_id)['counts'], 'refresh_seconds': 2,
            'instructions': 'Open this URL with open_in_codex as a browser tab in the current chat.'}


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--serve':
        serve(sys.argv[2], Path(sys.argv[3]))
    else:
        print(json.dumps(open_panel(os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID')), ensure_ascii=False))
