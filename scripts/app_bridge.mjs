// Use the application's bundled MCP server instead of implementing its private pipe.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const options = JSON.parse(input);
const child = spawn(process.execPath, [options.server], { stdio: ['pipe', 'pipe', 'pipe'] });
const pending = new Map();
let nextId = 1;
let diagnostics = '';
child.stderr.on('data', chunk => { diagnostics = (diagnostics + chunk.toString()).slice(-4000); });
createInterface({ input: child.stdout }).on('line', line => {
  try {
    const message = JSON.parse(line);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    message.error ? waiter.reject(new Error(message.error.message)) : waiter.resolve(message.result);
  } catch (error) {
    for (const waiter of pending.values()) waiter.reject(error);
    pending.clear();
  }
});
child.on('error', error => {
  for (const waiter of pending.values()) waiter.reject(error);
  pending.clear();
});
child.on('exit', code => {
  for (const waiter of pending.values()) waiter.reject(new Error(`App tools exited (${code}): ${diagnostics}`));
  pending.clear();
});
function request(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
}
const deadline = setTimeout(() => {
  for (const waiter of pending.values()) waiter.reject(new Error('App notification timed out; delivery may be unknown'));
  pending.clear();
  child.kill();
}, 25000);
try {
  await request('initialize', {
    protocolVersion: '2024-11-05', capabilities: {},
    clientInfo: { name: 'background-tasks-bridge', version: '0.1.1' }
  });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const catalog = await request('tools/list', {});
  const method = options.action === 'list_threads' ? 'list_threads' : 'send_message_to_thread';
  const tool = catalog.tools.find(tool => tool.name === method);
  if (!tool) throw new Error('Bundled app tools do not expose ' + method);
  if (options.probe) {
    console.log(JSON.stringify({ available: true, inputSchema: tool.inputSchema }));
  } else {
    const result = await request('tools/call', {
      name: tool.name,
      arguments: options.action === 'list_threads' ? { limit: 50 } : { threadId: options.thread_id, prompt: options.prompt },
      _meta: { threadId: options.thread_id }
    });
    if (result.isError) throw new Error(JSON.stringify(result.content));
    console.log(JSON.stringify({ delivered: options.action !== 'list_threads', result }));
  }
} catch (error) {
  console.error(JSON.stringify({ error: error.message }));
  process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  child.stdin.end();
  child.kill();
}
