"""Command-line access to the same background runner used by the plugin."""
import argparse
import json
import os
import jobs
from notifier import bridge

parser = argparse.ArgumentParser()
parser.add_argument('--thread-id', default=os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID'))
sub = parser.add_subparsers(dest='action', required=True)
run = sub.add_parser('run')
run.add_argument('--command', required=True)
run.add_argument('--cwd', required=True)
run.add_argument('--timeout', type=int, default=3600)
run.add_argument('--no-notify', action='store_true')
for name in ['get', 'cancel']:
    sub.add_parser(name).add_argument('task_id')
sub.add_parser('list')
sub.add_parser('probe')
args = parser.parse_args()
if args.action == 'probe':
    result = bridge({'probe': True})
elif args.action == 'run':
    result = jobs.start(args.command, args.cwd, args.thread_id, args.timeout, not args.no_notify)
elif args.action == 'get':
    result = jobs.get(args.task_id, args.thread_id)
elif args.action == 'cancel':
    result = jobs.cancel(args.task_id, args.thread_id)
else:
    result = {'tasks': jobs.recent(args.thread_id)}
print(json.dumps(result, ensure_ascii=False, indent=2))
