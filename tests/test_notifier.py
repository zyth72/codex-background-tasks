"""Notification payload tests with a mocked desktop bridge."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import notifier  # noqa: E402


class NotificationTests(unittest.TestCase):
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
