"""Small stdio MCP server: all tools return quickly, workers are detached."""
import json
import os
import sys
import jobs
import panel

UI_URI = "ui://background-tasks/panel-v0.1.9.html"
CATALOGS = {}


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


TOOLS = [
    {'name': 'background_tasks', 'title': '后台任务', 'description': 'Request a native conversation task panel on MCP Apps hosts.', 'inputSchema': schema({}), 'annotations': {'readOnlyHint': True}, '_meta': {'ui': {'resourceUri': UI_URI}, 'openai/ui': {'entrypoints': [{'type': 'thread'}]}}},
    {'name': 'read_task_panel', 'description': 'Read task panel data with host-provided conversation metadata on this call.', 'inputSchema': schema({'scope': {'type': 'string', 'enum': ['chat', 'all', 'thread']}, 'thread_id': {'type': 'string'}, 'catalog': {'type': 'boolean'}}), 'annotations': {'readOnlyHint': True}, '_meta': {'ui': {'visibility': ['app', 'model']}}},
    {'name': 'open_task_panel', 'description': 'Open a read-only live task dashboard showing counts, commands and status. Returns a local URL; use open_in_codex to show it in the current chat browser panel.',
     'inputSchema': schema({})},
    {'name': 'run_cli', 'description': 'Run an authorized CLI command in background. Send its result to this desktop chat when it exits. Executes in the local MCP process environment.',
     'inputSchema': schema({'command': {'type': 'string'}, 'cwd': {'type': 'string'},
        'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 604800, 'default': 3600},
        'notify': {'type': 'boolean', 'default': True}}, ['command', 'cwd'])},
    {'name': 'get_cli_task', 'description': 'Read status, output and notification outcome of a task in this chat.',
     'inputSchema': schema({'task_id': {'type': 'string'}}, ['task_id']), 'annotations': {'readOnlyHint': True}},
    {'name': 'list_cli_tasks', 'description': 'List recent CLI tasks belonging to this chat.',
     'inputSchema': schema({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}}), 'annotations': {'readOnlyHint': True}},
    {'name': 'cancel_cli_task', 'description': 'Request cancellation of a background CLI task and its process group in this chat.',
     'inputSchema': schema({'task_id': {'type': 'string'}}, ['task_id'])}
]


def caller(meta, allow_environment=True):
    for key in ['threadId', 'thread_id', 'codexThreadId', 'codex_thread_id', 'openai/threadId', 'openai/thread_id']:
        if isinstance(meta.get(key), str) and meta[key].strip():
            return meta[key]
    for value in meta.values():
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            if isinstance(value.get('thread_id'), str):
                return value['thread_id']
    nested = meta.get('thread', {})
    if isinstance(nested, dict) and isinstance(nested.get('id'), str):
        return nested['id']
    result = (os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID')) if allow_environment else None
    if not result:
        raise ValueError('Originating chat metadata unavailable')
    return result


def invoke(name, arguments, meta):
    tool = next((tool for tool in TOOLS if tool['name'] == name), None)
    if tool is None:
        raise ValueError('Unknown tool')
    if not isinstance(arguments, dict) or set(arguments) - set(tool['inputSchema']['properties']):
        raise ValueError('Unexpected tool arguments')
    if not set(tool['inputSchema']['required']).issubset(arguments):
        raise ValueError('Required tool arguments missing')
    thread = caller(meta, allow_environment=name not in ['read_task_panel', 'background_tasks'])
    if name in ['read_task_panel', 'background_tasks']:
        scope = arguments.get('scope', 'chat')
        if scope not in ['chat', 'all', 'thread']:
            raise ValueError('Invalid scope')
        catalog = CATALOGS.setdefault(thread, panel.ChatCatalog(thread))
        if arguments.get('catalog'):
            return catalog.read()
        selected = thread
        if scope == 'thread':
            selected = arguments.get('thread_id')
            if selected not in {chat['id'] for chat in catalog.read()['chats']}:
                raise ValueError('Unknown selected chat')
        result = panel.snapshot(selected, 'all' if scope == 'all' else 'chat')
        result['host_thread_id'] = thread
        return result
    if name == 'open_task_panel':
        return panel.open_panel(thread)
    if name == 'run_cli':
        return jobs.start(thread_id=thread, **arguments)
    if name == 'get_cli_task':
        return jobs.get(thread_id=thread, **arguments)
    if name == 'list_cli_tasks':
        return {'tasks': jobs.recent(thread, **arguments)}
    return jobs.cancel(thread_id=thread, **arguments)


def dispatch(message):
    method = message.get('method')
    params = message.get('params') or {}
    if method == 'initialize':
        offered = params.get('protocolVersion', '2024-11-05')
        version = offered if offered in ['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'] else '2024-11-05'
        return {'protocolVersion': version, 'capabilities': {'tools': {}, 'resources': {}},
                'serverInfo': {'name': 'background-tasks', 'version': '0.1.9'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'resources/list':
        return {'resources': [{'uri': UI_URI, 'name': 'Background task panel', 'mimeType': 'text/html;profile=mcp-app'}]}
    if method == 'resources/read':
        if params.get('uri') != UI_URI:
            raise ValueError('Unknown UI resource')
        import native_panel
        return {'contents': [{'uri': params['uri'], 'mimeType': 'text/html;profile=mcp-app', 'text': native_panel.html(), '_meta': {'ui': {'prefersBorder': True}}}]}
    if method == 'tools/call':
        try:
            result = invoke(params['name'], params.get('arguments', {}), params.get('_meta', {}))
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'structuredContent': result, 'isError': False}
        except (OSError, ValueError, TypeError, KeyError) as error:
            return {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}
    raise ValueError('Unknown MCP method: ' + str(method))


def main():
    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError('JSON-RPC message must be an object')
            if 'id' not in message:
                continue
            response = {'jsonrpc': '2.0', 'id': message['id'], 'result': dispatch(message)}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': message.get('id') if isinstance(message, dict) else None,
                        'error': {'code': -32601 if isinstance(error, ValueError) and str(error).startswith('Unknown MCP method') else -32600, 'message': str(error)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
