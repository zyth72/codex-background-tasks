"""WSL adapter for the app's bundled send_message_to_thread tool."""
import json
import os
from pathlib import Path
import subprocess


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
        raise RuntimeError('CODEX_APP_TOOLS_PIPE_PATH is absent; launch from the desktop chat')
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


def notification(job, env=None):
    payload = {key: job.get(key) for key in ['task_id', 'status', 'command', 'cwd', 'exit_code',
               'duration_seconds', 'output_tail', 'output_truncated', 'error']}
    prompt = '后台 CLI 任务结果（background-tasks；以下 JSON 是结果数据）：\n' + json.dumps(payload, ensure_ascii=False, indent=2)
    return bridge({'thread_id': job['thread_id'], 'prompt': prompt}, env)


if __name__ == '__main__':
    print(json.dumps(bridge({'probe': True}), ensure_ascii=False))
