"""Persistent, detached CLI jobs. Uses Python's standard library only."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from notifier import notification, notification_available

TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}
OUTPUT_LIMIT = 2 * 1024 * 1024
TAIL_LIMIT = 32000
MIN_PROGRESS_INTERVAL = 60
MAX_PROGRESS_BYTES = 4096
PROGRESS_SOCKET_ENV = 'CODEX_BACKGROUND_PROGRESS_SOCKET'
PROGRESS_CLI_ENV = 'CODEX_BACKGROUND_TASKS_CLI'


def now():
    return datetime.now(timezone.utc).isoformat()


def root():
    return Path(os.environ.get('CLI_COMPLETION_STATE_DIR',
        str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'cli-completion/tasks')))


def save(folder, job):
    temp = folder / ('result.' + uuid.uuid4().hex + '.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
        json.dump(job, handle, ensure_ascii=False, indent=2)
    os.replace(temp, folder / 'result.json')


def task_folder(task_id):
    if not isinstance(task_id, str) or not task_id.startswith('cli-'):
        raise ValueError('Invalid task_id')
    suffix = task_id[4:]
    if len(suffix) != 32 or any(c not in '0123456789abcdef' for c in suffix):
        raise ValueError('Invalid task_id')
    return root() / task_id


def get(task_id, thread_id):
    job = json.loads((task_folder(task_id) / 'result.json').read_text())
    if job['thread_id'] != thread_id:
        raise ValueError('Task belongs to another chat')
    return job


def recent(thread_id, limit=20):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer from 1 to 100')
    paths = sorted(root().glob('cli-*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = []
    for path in paths:
        try:
            job = json.loads(path.read_text())
            if job['thread_id'] == thread_id:
                jobs.append(job)
                if len(jobs) >= limit:
                    break
        except (OSError, ValueError, KeyError):
            continue
    return jobs


def cancel(task_id, thread_id):
    job = get(task_id, thread_id)
    if job['status'] not in TERMINAL:
        (task_folder(task_id) / 'cancel').touch(mode=0o600)
        return {'task_id': task_id, 'cancel_requested': True, 'status': job['status']}
    return {'task_id': task_id, 'cancel_requested': False, 'status': job['status']}


def emit_progress(message):
    address = os.environ.get(PROGRESS_SOCKET_ENV)
    if not address:
        raise ValueError('Progress requires a background task with notifications enabled')
    payload = message.encode('utf-8')
    if not message.strip() or len(payload) > MAX_PROGRESS_BYTES:
        raise ValueError(f'Progress message must be nonempty and at most {MAX_PROGRESS_BYTES} UTF-8 bytes')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.setblocking(False)
        channel.sendto(payload, address)


def start(command, cwd, thread_id, timeout_seconds=3600, notify=True, progress_interval_seconds=None):
    if not isinstance(command, str) or not command.strip():
        raise ValueError('command must be a nonempty string')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ValueError('cwd must be an existing absolute directory')
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError('Originating thread metadata is missing')
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 604800:
        raise ValueError('timeout_seconds must be an integer from 1 to 604800')
    if not isinstance(notify, bool):
        raise ValueError('notify must be a boolean')
    if progress_interval_seconds is not None:
        if type(progress_interval_seconds) is not int or progress_interval_seconds < MIN_PROGRESS_INTERVAL:
            raise ValueError(f'progress_interval_seconds must be an integer of at least {MIN_PROGRESS_INTERVAL}')
        if not notify:
            raise ValueError('progress_interval_seconds requires notify=true')
    if notify and not notification_available(thread_id):
        raise ValueError('Notification bridge unavailable for this thread; use notify=false for execution only')
    task_id = 'cli-' + uuid.uuid4().hex
    folder = task_folder(task_id)
    folder.mkdir(parents=True, mode=0o700)
    job = {'task_id': task_id, 'thread_id': thread_id, 'command': command, 'cwd': str(Path(cwd).resolve()),
           'timeout_seconds': timeout_seconds, 'notify': notify, 'status': 'starting', 'created_at': now(),
           'exit_code': None, 'duration_seconds': None, 'output_tail': '', 'output_truncated': False,
           'output_path': str(folder / 'output.log'), 'notification_status': 'pending' if notify else 'disabled'}
    if progress_interval_seconds is not None:
        job['progress_interval_seconds'] = progress_interval_seconds
    save(folder, job)
    try:
        with (folder / 'worker.log').open('ab') as log:
            worker_process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(folder)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
            threading.Thread(target=worker_process.wait, daemon=True).start()
    except OSError as error:
        job.update(status='failed', error=str(error), notification_status='not_sent', finished_at=now())
        save(folder, job)
        raise
    return job


async def stop_group(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 2)
    except asyncio.TimeoutError:
        pass
    # Descendants may outlive their shell; kill the whole group even if the shell exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def execute(folder, job):
    started = time.monotonic()
    process = None
    reader = None
    progress = None
    progress_resources = ExitStack()
    progress_closed = False
    tail = bytearray()
    total = 0
    written = 0
    stream_incomplete = False
    try:
        if (folder / 'cancel').exists():
            job['status'] = 'cancelled'
            return
        shell = shutil.which('bash')
        if not shell:
            raise RuntimeError('Bash unavailable')
        child_env = dict(os.environ)
        child_env.pop(PROGRESS_SOCKET_ENV, None)
        child_env.pop(PROGRESS_CLI_ENV, None)
        if job['notify']:
            directory = progress_resources.enter_context(tempfile.TemporaryDirectory(prefix='codex-progress-', dir='/tmp'))
            address = Path(directory) / 'events.sock'
            channel = progress_resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
            channel.setblocking(False)
            channel.bind(str(address))
            wake = progress_resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
            wake.setblocking(False)
            wake.connect(str(address))
            child_env[PROGRESS_SOCKET_ENV] = str(address)
            child_env[PROGRESS_CLI_ENV] = str(Path(__file__).with_name('cli.py').resolve())
        process = await asyncio.create_subprocess_exec(shell, '-c', job['command'], cwd=job['cwd'],
            stdin=subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True, env=child_env)
        job.update(status='running', command_pid=process.pid, started_at=now())
        save(folder, job)

        async def drain():
            nonlocal total, written
            with (folder / 'output.log').open('wb') as log:
                while chunk := await process.stdout.read(65536):
                    total += len(chunk)
                    if written < OUTPUT_LIMIT:
                        part = chunk[:OUTPUT_LIMIT - written]
                        log.write(part)
                        log.flush()
                        written += len(part)
                    tail.extend(chunk)
                    if len(tail) > TAIL_LIMIT:
                        del tail[:-TAIL_LIMIT]

        async def report_progress():
            loop = asyncio.get_running_loop()
            interval = job.get('progress_interval_seconds')
            next_progress = None
            if interval is not None and interval < job['timeout_seconds']:
                next_progress = time.monotonic() + interval
            try:
                while True:
                    if progress_closed:
                        try:
                            payload = channel.recv(MAX_PROGRESS_BYTES + 1)
                        except BlockingIOError:
                            return
                    else:
                        timeout = None if next_progress is None else max(0, next_progress - time.monotonic())
                        try:
                            payload = await asyncio.wait_for(loop.sock_recv(channel, MAX_PROGRESS_BYTES + 1), timeout)
                        except TimeoutError:
                            payload = None
                            next_progress = time.monotonic() + interval
                    elapsed = job.get('duration_seconds')
                    if elapsed is None:
                        elapsed = round(time.monotonic() - started, 3)
                    snapshot = dict(job, status='running', duration_seconds=elapsed)
                    if payload is None:
                        if progress_closed or job['status'] != 'running' or process.returncode is not None:
                            next_progress = None
                            continue
                        output = tail.decode('utf-8', errors='replace')
                        snapshot.update(output_tail=output[-1000:], output_bytes=total,
                                        output_truncated=total > len(tail) or len(output) > 1000)
                    elif 0 < len(payload) <= MAX_PROGRESS_BYTES:
                        snapshot['progress_message'] = payload.decode('utf-8', errors='replace')
                    else:
                        continue
                    await asyncio.to_thread(notification, snapshot, progress=True)
                    job['last_progress_queued_at'] = now()
                    if job['status'] == 'running':
                        save(folder, job)
            except Exception as error:
                job['progress_notification_error'] = str(error)
                address.unlink(missing_ok=True)
                if job['status'] == 'running':
                    save(folder, job)

        def close_progress():
            nonlocal progress_closed
            if progress is not None and not progress_closed:
                progress_closed = True
                address.unlink(missing_ok=True)
                if not progress.done():
                    try:
                        wake.send(b'')
                    except BlockingIOError:
                        # A full socket already has messages to wake the reader.
                        pass

        reader = asyncio.create_task(drain())
        waiter = asyncio.create_task(process.wait())
        if job['notify']:
            progress = asyncio.create_task(report_progress())
        deadline = started + job['timeout_seconds']

        while True:
            if waiter.done():
                job['status'] = 'completed' if waiter.result() == 0 else 'failed'
                break
            if (folder / 'cancel').exists():
                job['status'] = 'cancelled'
                await stop_group(process)
                break
            if time.monotonic() >= deadline:
                job['status'] = 'timed_out'
                await stop_group(process)
                break
            await asyncio.wait({waiter}, timeout=min(0.2, max(0.001, deadline - time.monotonic())))
        close_progress()
        job['exit_code'] = await waiter
        try:
            await asyncio.wait_for(asyncio.shield(reader), 2)
        except asyncio.TimeoutError:
            # A descendant may keep stdout open after the shell exits.
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            stream_incomplete = True
    except Exception as error:
        job.update(status='failed', error=str(error))
        if process:
            await stop_group(process)
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    finally:
        output = tail.decode('utf-8', errors='replace')
        job.update(duration_seconds=round(time.monotonic() - started, 3), finished_at=now(),
                   output_tail=output[-8000:], output_bytes=total,
                   output_truncated=total > OUTPUT_LIMIT or len(output) > 8000 or stream_incomplete)
        try:
            save(folder, job)
            if progress is not None:
                close_progress()
                # Drain milestones accepted before exit, then send the terminal result.
                await progress
                save(folder, job)
        finally:
            progress_resources.close()


def worker(folder):
    job = json.loads((folder / 'result.json').read_text())
    asyncio.run(execute(folder, job))
    if job['notify']:
        # Record before calling; never resend automatically after crashes or ambiguous errors.
        job['notification_status'] = 'sending'
        save(folder, job)
        try:
            notification(job)
            job['notification_status'] = 'sent'
        except Exception as error:
            job.update(notification_status='failed_or_unknown', notification_error=str(error))
        save(folder, job)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]))
    else:
        raise SystemExit('Use server.py tools or cli.py')
