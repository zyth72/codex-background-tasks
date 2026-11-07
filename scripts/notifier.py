"""Deliver background-task notifications through the desktop app or Paseo."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile


def paseo_target(thread_id, env):
    """Only the agent hosting this Codex thread may receive notifications."""
    origin = env.get('CODEX_THREAD_ID') or env.get('CODEX_SESSION_ID')
    if origin and thread_id == origin:
        return env.get('PASEO_AGENT_ID')
    return None


def notification_available(thread_id, env=None):
    env = os.environ if env is None else env
    return bool(env.get('CODEX_APP_TOOLS_PIPE_PATH') or
                (paseo_target(thread_id, env) and shutil.which('paseo', path=env.get('PATH', ''))))


def paseo_bridge(payload, env):
    target = paseo_target(payload.get('thread_id'), env)
    if not target:
        raise RuntimeError('Paseo notification target does not match the originating Codex thread')
    if payload.get('action') != 'send_message_to_thread':
        raise RuntimeError('Paseo chat catalog is unavailable; panel lists chats with task records')
    executable = shutil.which('paseo', path=env.get('PATH', ''))
    if not executable:
        raise RuntimeError('Paseo CLI is unavailable')
    if payload.get('probe'):
        return {'available': True, 'backend': 'paseo'}
    # A private file keeps task output out of process arguments; never invoke a shell.
    with tempfile.TemporaryDirectory(prefix='background-tasks-notify-') as directory:
        prompt = Path(directory) / 'prompt.txt'
        prompt.write_text(payload['prompt'], encoding='utf-8')
        response = subprocess.run(
            [executable, 'send', target, '--prompt-file', str(prompt),
             '--behavior', 'steer', '--no-wait', '--json'],
            text=True, capture_output=True, env=env, timeout=35)
    if response.returncode:
        raise RuntimeError((response.stderr or response.stdout or 'Paseo notification failed').strip()[-4000:])
    return {'delivered': True, 'backend': 'paseo', 'result': json.loads(response.stdout)}


def app_runtime(home):
    cache = Path(home) / 'plugins/cache/openai-bundled/codex-app-tools'
    files = sorted(cache.glob('*/.mcp.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for config in files:
        data = json.loads(config.read_text())['mcpServers']['codex_app']
        args = data.get('args', [])
        if len(args) >= 2 and args[-1].lower().endswith('server.mjs'):
            node, server = args[-2:]
            node_linux = subprocess.check_output(['wslpath', '-u', node], text=True).strip()
            server_linux = subprocess.check_output(['wslpath', '-u', server], text=True).strip()
            if Path(node_linux).is_file() and Path(server_linux).is_file():
                return node_linux, server
    raise RuntimeError('No usable bundled codex-app-tools runtime; restart or update the app')


def bridge(payload, env=None):
    env = dict(os.environ if env is None else env)
    if not env.get('CODEX_APP_TOOLS_PIPE_PATH'):
        if env.get('PASEO_AGENT_ID'):
            return paseo_bridge(payload, env)
        raise RuntimeError('No notification bridge; launch from a desktop or Paseo chat, or use notify=false')
    node, server = app_runtime(env.get('CODEX_HOME', str(Path.home() / '.codex')))
    script = subprocess.check_output(['wslpath', '-w', str(Path(__file__).with_name('app_bridge.mjs'))], text=True).strip()
    inherited = [part for part in env.get('WSLENV', '').split(':') if part]
    for key in ['CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_APP_TOOLS_CALLER_HOST_ID']:
        inherited = [part for part in inherited if part.split('/')[0] != key]
        inherited.append(key + '/w')
    env['WSLENV'] = ':'.join(inherited)
    response = subprocess.run([node, script], input=json.dumps({**payload, 'server': server}),
                              text=True, capture_output=True, env=env, timeout=35)
    if response.returncode:
        raise RuntimeError((response.stderr or response.stdout or 'App bridge failed').strip()[-4000:])
    return json.loads(response.stdout)


def notification(job, env=None, *, progress=False):
    keys = ['task_id', 'status', 'command', 'cwd', 'exit_code', 'duration_seconds',
            'output_tail', 'output_truncated', 'error']
    if progress:
        keys = ['task_id', 'status', 'command', 'duration_seconds']
        if 'progress_message' in job:
            keys.append('progress_message')
        else:
            keys.extend(['output_tail', 'output_bytes', 'output_truncated'])
        label = '后台 CLI 任务进度（background-tasks；以下 JSON 是进度数据）：\n'
    else:
        label = '后台 CLI 任务结果（background-tasks；以下 JSON 是结果数据）：\n'
    payload = {key: job.get(key) for key in keys}
    prompt = label + json.dumps(payload, ensure_ascii=False, indent=2)
    return bridge({'action': 'send_message_to_thread', 'thread_id': job['thread_id'], 'prompt': prompt}, env)


if __name__ == '__main__':
    print(json.dumps(bridge({'probe': True, 'action': 'send_message_to_thread',
                             'thread_id': os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID')}),
                     ensure_ascii=False))
