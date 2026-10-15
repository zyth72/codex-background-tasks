"""Runner tests: temporary state directory, mocked notifications, no desktop calls.

Run from the repository root with:

    python3 -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import jobs  # noqa: E402
import panel  # noqa: E402


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.home = base / 'mock-home'
        self.state = base / 'state'
        self.cwd = base / 'cwd'
        self.cwd.mkdir(parents=True)
        # jobs reads CODEX_HOME / CLI_COMPLETION_STATE_DIR at call time, but the
        # environment must be in place before any worker subprocess is spawned.
        # The real desktop notification pipe is removed so no test can deliver a message.
        env = os.environ.copy()
        env['CODEX_HOME'] = str(self.home)
        env['CLI_COMPLETION_STATE_DIR'] = str(self.state)
        env.pop('CODEX_APP_TOOLS_PIPE_PATH', None)
        env.pop('CODEX_APP_TOOLS_CALLER_HOST_ID', None)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait_for(self, task_id, thread_id, statuses, timeout=20):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = jobs.get(task_id, thread_id)
            except (OSError, ValueError):
                last = None
            if last and last.get('status') in statuses:
                return last
            time.sleep(0.05)
        self.fail(f'task {task_id} did not reach {statuses}; last record: {last}')

    def make_notify_folder(self, notify):
        folder = jobs.root() / ('cli-' + uuid.uuid4().hex)
        folder.mkdir(parents=True, mode=0o700)
        job = {'task_id': folder.name, 'thread_id': 'thread-a', 'command': "printf 'notify\\n'",
               'cwd': str(self.cwd), 'timeout_seconds': 60, 'notify': notify, 'status': 'starting',
               'created_at': jobs.now(), 'exit_code': None, 'duration_seconds': None,
               'output_tail': '', 'output_truncated': False, 'output_path': str(folder / 'output.log'),
               'notification_status': 'pending' if notify else 'disabled'}
        jobs.save(folder, job)
        return folder

    def test_state_directory_follows_environment_at_call_time(self):
        self.assertEqual(jobs.root(), self.state)
        alternate = Path(self.temporary.name) / 'alternate'
        with mock.patch.dict(os.environ, {'CLI_COMPLETION_STATE_DIR': str(alternate)}):
            self.assertEqual(jobs.root(), alternate)

    def test_short_command_completes_without_notification(self):
        job = jobs.start("printf 'hello\\n'", str(self.cwd), 'thread-a', timeout_seconds=60, notify=False)
        result = self.wait_for(job['task_id'], 'thread-a', jobs.TERMINAL)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['notification_status'], 'disabled')
        self.assertIn('hello', result['output_tail'])

    def test_failing_command_reports_exit_code(self):
        job = jobs.start('exit 7', str(self.cwd), 'thread-a', timeout_seconds=60, notify=False)
        result = self.wait_for(job['task_id'], 'thread-a', jobs.TERMINAL)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exit_code'], 7)

    def test_cancel_stops_a_running_task(self):
        job = jobs.start('sleep 30', str(self.cwd), 'thread-a', timeout_seconds=120, notify=False)
        self.wait_for(job['task_id'], 'thread-a', {'starting', 'running'})
        cancelled = jobs.cancel(job['task_id'], 'thread-a')
        self.assertTrue(cancelled['cancel_requested'])
        result = self.wait_for(job['task_id'], 'thread-a', {'cancelled'})
        self.assertEqual(result['status'], 'cancelled')

    def test_timeout_stops_a_long_task(self):
        job = jobs.start('sleep 30', str(self.cwd), 'thread-a', timeout_seconds=1, notify=False)
        result = self.wait_for(job['task_id'], 'thread-a', {'timed_out'})
        self.assertEqual(result['status'], 'timed_out')

    def test_tasks_are_isolated_by_thread(self):
        first = jobs.start("printf 'first'", str(self.cwd), 'thread-a', timeout_seconds=60, notify=False)
        second = jobs.start("printf 'second'", str(self.cwd), 'thread-b', timeout_seconds=60, notify=False)
        self.wait_for(first['task_id'], 'thread-a', jobs.TERMINAL)
        self.wait_for(second['task_id'], 'thread-b', jobs.TERMINAL)
        self.assertEqual([task['task_id'] for task in jobs.recent('thread-a')], [first['task_id']])
        self.assertEqual([task['task_id'] for task in jobs.recent('thread-b')], [second['task_id']])
        with self.assertRaises(ValueError):
            jobs.get(first['task_id'], 'thread-b')
        with self.assertRaises(ValueError):
            jobs.cancel(first['task_id'], 'thread-b')

    def test_notification_failure_is_recorded_without_delivery(self):
        folder = self.make_notify_folder(notify=True)
        with mock.patch.object(jobs, 'notification',
                               side_effect=RuntimeError('mock notification pipe down')) as notifying:
            jobs.worker(folder)
        result = json.loads((folder / 'result.json').read_text())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['notification_status'], 'failed_or_unknown')
        self.assertIn('mock notification pipe down', result['notification_error'])
        self.assertEqual(notifying.call_count, 1)

    def test_notification_success_is_recorded(self):
        folder = self.make_notify_folder(notify=True)
        with mock.patch.object(jobs, 'notification', return_value={'delivered': True}) as notifying:
            jobs.worker(folder)
        result = json.loads((folder / 'result.json').read_text())
        self.assertEqual(result['notification_status'], 'sent')
        self.assertEqual(notifying.call_count, 1)

    def test_notify_false_never_calls_notification(self):
        folder = self.make_notify_folder(notify=False)
        with mock.patch.object(jobs, 'notification') as notifying:
            jobs.worker(folder)
        result = json.loads((folder / 'result.json').read_text())
        self.assertEqual(result['notification_status'], 'disabled')
        notifying.assert_not_called()

    def test_notify_true_requires_desktop_pipe(self):
        with self.assertRaises(ValueError):
            jobs.start("printf 'x'", str(self.cwd), 'thread-a', timeout_seconds=60, notify=True)

    def test_panel_snapshot_reads_only_job_state(self):
        job = jobs.start("printf 'panel'", str(self.cwd), 'thread-a', timeout_seconds=60, notify=False)
        self.wait_for(job['task_id'], 'thread-a', jobs.TERMINAL)
        snapshot = panel.snapshot('thread-a')
        self.assertEqual(snapshot['counts']['completed'], 1)
        self.assertEqual(snapshot['counts']['total'], 1)
        other = panel.snapshot('thread-b')
        self.assertEqual(other['counts']['total'], 0)


if __name__ == '__main__':
    unittest.main()
