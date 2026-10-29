/* Optional local-only CDP transport. Inactive without the project bridge lease.
 * Does not change browser/instance proxy settings or intercept Arena requests.
 */
(() => {
  if (globalThis.Arena2APILocalTransport) return;
  const original = globalThis.fetch.bind(globalThis), pending = new Map();
  let port = 9090, expires = 0, serial = 0, scope = null;
  function eligible(input, init) {
    if (typeof input !== 'string') return false;
    try {
      const u = new URL(input), method = String(init?.method || 'GET').toUpperCase();
      if (u.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(u.hostname) || Number(u.port) !== port || u.username || u.password || u.hash) return false;
      if (u.pathname === '/v1/conversation/attachment') return method === 'GET' && /^\?client_id=[0-9a-f]{32}&request_id=[0-9a-f]{32}&token=[0-9a-f]{64}&file_index=\d{1,2}&offset=\d{1,9}$/.test(u.search);
      if (u.pathname === '/health') return method === 'GET' && !u.search;
      if (['/v1/conversation/offer','/v1/conversation/result'].includes(u.pathname)) return method === 'POST' && !u.search;
      if (u.pathname === '/v1/conversation/command') return method === 'GET' && /^\?client_id=[0-9a-f]{32}$/.test(u.search);
    } catch {}
    return false;
  }
  globalThis.Arena2APILocalTransport = {
    renew(value, source) { if (!Number.isInteger(value) || value < 1024 || value > 65535) throw Error('Invalid local port'); if(source!==undefined){if(!source||!/^([0-9a-f]{64})$/.test(source.id)||typeof source.name!=='string'||source.name.length>80||!source.name)throw Error('Invalid source scope');scope={id:source.id,name:source.name};} port=value;expires=Date.now()+12000; },
    endpoint() { return this.active() ? 'http://127.0.0.1:'+port : null; },
    source() { return this.active() && scope ? {...scope} : null; },
    active() { return Date.now()<expires && typeof globalThis.__arena2apiLocalRequest==='function'; },
    reply(id, result) {
      const p=pending.get(id);if(!p)return;pending.delete(id);p.cleanup();
      if(result.error)p.reject(new Error('本机桥接请求失败；未自动重发'));
      else p.resolve(new Response(result.body,{status:result.status,headers:{'Content-Type':'application/json'}}));
    },
    disable() {expires=0;for(const p of pending.values()){p.cleanup();p.reject(new Error('本机桥接已断开'));}pending.clear();}
  };
  globalThis.fetch = function(input, init={}) {
    if(!eligible(input,init)||!globalThis.Arena2APILocalTransport.active())return original(input,init);
    if(init.signal?.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
    if(pending.size>=16)return Promise.reject(new Error('本机桥接繁忙'));
    if(init.body!==undefined&&typeof init.body!=='string')return Promise.reject(new Error('本机桥接仅支持JSON文本'));
    const id=String(++serial)+'-'+Date.now();
    return new Promise((resolve,reject)=>{
      const cancel=()=>{const p=pending.get(id);if(!p)return;pending.delete(id);p.cleanup();try{globalThis.__arena2apiLocalRequest(JSON.stringify({id,cancel:true}));}catch{}reject(new DOMException('Aborted','AbortError'));};
      const timer=setTimeout(cancel,30000);
      const cleanup=()=>{clearTimeout(timer);init.signal?.removeEventListener('abort',cancel);};
      pending.set(id,{resolve,reject,cleanup});init.signal?.addEventListener('abort',cancel,{once:true});
      try{globalThis.__arena2apiLocalRequest(JSON.stringify({id,url:input,method:String(init.method||'GET').toUpperCase(),body:init.body}));}
      catch(e){pending.delete(id);cleanup();reject(new Error('本机桥接不可用；未自动重发'));}
    });
  };
})();
