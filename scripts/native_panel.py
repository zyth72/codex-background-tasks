"""Reuse dashboard presentation with a host MCP Apps transport, no HTTP token."""
from pathlib import Path


BRIDGE = r'''
const pendingHostCalls=new Map();let nextHostId=1;
function hostRequest(method,params){return new Promise((resolve,reject)=>{
 const id=nextHostId++,timer=setTimeout(()=>{pendingHostCalls.delete(id);reject(Error('宿主未响应扩展请求，请使用浏览器面板。'))},10000);
 pendingHostCalls.set(id,{resolve,reject,timer});
 window.parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
})}
window.addEventListener('message',event=>{
 if(event.source!==window.parent||event.data?.jsonrpc!=='2.0')return;
 const message=event.data,call=pendingHostCalls.get(message.id);
 if(call){clearTimeout(call.timer);pendingHostCalls.delete(message.id);message.error?call.reject(Error(message.error.message||'宿主请求失败')):call.resolve(message.result);return}
 if(message.method==='ui/notifications/host-context-changed'){
  lastChatRefresh=0;lastSignature='';loadChats(true);refresh(true);
 }
});
const hostReady=hostRequest('ui/initialize',{protocolVersion:'2026-01-26',appInfo:{name:'background-tasks',version:'0.1.9'},appCapabilities:{}}).then(result=>{
 window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{}},'*');return result;
});
// fetch-shaped adapter keeps the shared renderer free of host-specific details.
window.fetch=async function(url){
 await hostReady;
 const path=new URL(url,'https://panel.invalid'),arguments_=path.pathname==='/api/chats'?{catalog:true}:Object.fromEntries(path.searchParams);
 const reply=await hostRequest('tools/call',{name:'read_task_panel',arguments:arguments_});
 if(reply?.isError)throw Error(reply.content?.find(item=>item.type==='text')?.text||'无法获取宿主聊天上下文');
 if(!reply?.structuredContent)throw Error('宿主未返回面板数据');
 return {ok:true,status:200,json:async()=>reply.structuredContent};
};
'''


def html():
    source = Path(__file__).with_name('panel.html').read_text()
    source = source.replace("if(location.hash)history.replaceState(null,'',location.pathname+location.search);", '')
    source = source.replace('const nativeMode=false;', 'const nativeMode=true;')
    source = source.replace('打开面板的聊天', '宿主关联的聊天')
    source = source.replace('浏览器面板不会自动跟随 Codex 切换聊天。', '会话由宿主在每次工具请求时提供；若宿主不提供上下文，将显示错误。')
    return source.replace('const $=id=>', BRIDGE + '\nconst $=id=>')
