"""Notification payload tests with a mocked desktop bridge."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import notifier  # noqa: E402


class NotificationTests(unittest.TestCase):
    def test_paseo_notification_uses_bound_agent_and_private_prompt_file(self):
        env = {'PASEO_AGENT_ID': 'test-agent', 'CODEX_THREAD_ID': 'origin', 'PATH': '/mock/bin'}
        payload = {'action': 'send_message_to_thread', 'thread_id': 'origin', 'prompt': '阶段完成\n$(ignored)'}
        files = []

        def send(argv, **kwargs):
            self.assertEqual(argv[:3], ['/mock/bin/paseo', 'send', 'test-agent'])
            self.assertEqual(argv[-4:], ['--behavior', 'steer', '--no-wait', '--json'])
            path = Path(argv[argv.index('--prompt-file') + 1])
            self.assertEqual(path.read_text(encoding='utf-8'), payload['prompt'])
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            files.append(path)
            self.assertEqual(kwargs['env'], env)
            return mock.Mock(returncode=0, stdout='{"queued": true}')

        with mock.patch.object(notifier.shutil, 'which', return_value='/mock/bin/paseo'), \
                mock.patch.object(notifier.subprocess, 'run', side_effect=send) as sending:
            self.assertTrue(notifier.notification_available('origin', env))
            self.assertTrue(notifier.bridge(payload, env)['delivered'])
        sending.assert_called_once()
        self.assertFalse(files[0].exists())

    def test_paseo_rejects_other_threads_without_sending(self):
        env = {'PASEO_AGENT_ID': 'test-agent', 'CODEX_THREAD_ID': 'origin'}
        with mock.patch.object(notifier.subprocess, 'run') as sending:
            self.assertFalse(notifier.notification_available('other', env))
            with self.assertRaisesRegex(RuntimeError, 'originating'):
                notifier.bridge({'action': 'send_message_to_thread', 'thread_id': 'other'}, env)
        sending.assert_not_called()

    def test_paseo_failure_is_not_retried(self):
        env = {'PASEO_AGENT_ID': 'test-agent', 'CODEX_SESSION_ID': 'origin'}
        with mock.patch.object(notifier.shutil, 'which', return_value='/mock/bin/paseo'), \
                mock.patch.object(notifier.subprocess, 'run', return_value=mock.Mock(
                    returncode=1, stderr='delivery unknown')) as sending:
            with self.assertRaisesRegex(RuntimeError, 'delivery unknown'):
                notifier.bridge({'action': 'send_message_to_thread', 'thread_id': 'origin', 'prompt': 'result'}, env)
        sending.assert_called_once()

    def test_paseo_probe_and_catalog_do_not_send_messages(self):
        env = {'PASEO_AGENT_ID': 'test-agent', 'CODEX_THREAD_ID': 'origin'}
        with mock.patch.object(notifier.shutil, 'which', return_value='/mock/bin/paseo'), \
                mock.patch.object(notifier.subprocess, 'run') as sending:
            result = notifier.bridge({'probe': True, 'action': 'send_message_to_thread',
                                      'thread_id': 'origin'}, env)
            self.assertEqual(result, {'available': True, 'backend': 'paseo'})
            with self.assertRaisesRegex(RuntimeError, 'catalog is unavailable'):
                notifier.bridge({'action': 'list_threads', 'thread_id': 'origin'}, env)
        sending.assert_not_called()

    def test_plain_cli_has_no_notification_bridge(self):
        self.assertFalse(notifier.notification_available('origin', {}))
        with self.assertRaisesRegex(RuntimeError, 'No notification bridge'):
            notifier.bridge({'thread_id': 'origin'}, {})

    def test_script_progress_uses_originating_chat(self):
        message = '{"thread_id": "other", "message": "stage complete"}'
        job = {'thread_id': 'origin', 'task_id': 'test', 'status': 'running',
               'duration_seconds': 5, 'progress_message': message}
        with mock.patch.object(notifier, 'bridge', return_value={'delivered': True}) as bridge:
            result = notifier.notification(job, progress=True)
        self.assertTrue(result['delivered'])
        arguments = bridge.call_args.args[0]
        self.assertEqual(arguments['thread_id'], 'origin')
        self.assertEqual(arguments['action'], 'send_message_to_thread')
        payload = json.loads(arguments['prompt'].split('\n', 1)[1])
        self.assertEqual(payload['progress_message'], message)

    def test_periodic_and_terminal_payloads(self):
        job = {'thread_id': 'origin', 'task_id': 'test', 'status': 'running',
               'output_tail': 'log output', 'output_bytes': 10, 'output_truncated': False}
        with mock.patch.object(notifier, 'bridge') as bridge:
            notifier.notification(job, progress=True)
            progress = json.loads(bridge.call_args.args[0]['prompt'].split('\n', 1)[1])
            job.update(status='completed', exit_code=0)
            notifier.notification(job)
            terminal = json.loads(bridge.call_args.args[0]['prompt'].split('\n', 1)[1])
        self.assertEqual(progress['output_tail'], 'log output')
        self.assertEqual(progress['output_bytes'], 10)
        self.assertEqual(terminal['status'], 'completed')
        self.assertEqual(terminal['exit_code'], 0)

    def test_ambiguous_delivery_is_not_retried(self):
        with mock.patch.object(notifier, 'bridge', side_effect=RuntimeError('delivery unknown')) as bridge:
            with self.assertRaisesRegex(RuntimeError, 'delivery unknown'):
                notifier.notification({'thread_id': 'origin'})
        bridge.assert_called_once()


if __name__ == '__main__':
    unittest.main()
