/* Local conversation-only bridge for one explicitly selected FACET instance.
 * Node 22+. No dependencies, proxy changes, account requests, or token handling.
 */
'use strict';
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const ROOT=path.resolve(__dirname,'..');
function validateRequest(q,port){
 if(!q||typeof q.id!=='string'||q.id.length>80||typeof q.url!=='string')throw Error('Invalid request');
 const u=new URL(q.url);
 if(u.protocol!=='http:'||!['127.0.0.1','localhost'].includes(u.hostname)||Number(u.port)!==port||u.username||u.password||u.hash)throw Error('Non-local destination rejected');
 const allowed=(q.method==='GET'&&u.pathname==='/health'&&!u.search)||
 (q.method==='GET'&&u.pathname==='/v1/conversation/command'&&/^\?client_id=[0-9a-f]{32}$/.test(u.search))||
 (q.method==='GET'&&u.pathname==='/v1/conversation/attachment'&&/^\?client_id=[0-9a-f]{32}&request_id=[0-9a-f]{32}&token=[0-9a-f]{64}&file_index=\d{1,2}&offset=\d{1,9}$/.test(u.search))||
 (q.method==='POST'&&['/v1/conversation/offer','/v1/conversation/result'].includes(u.pathname)&&!u.search);
 if(!allowed||q.body!==undefined&&(typeof q.body!=='string'||Buffer.byteLength(q.body)>1600000))throw Error('Unsupported local operation');
 return u.pathname+u.search;
}
function direct(port,route,method='GET',body,signal){
 return new Promise((resolve,reject)=>{
  const req=http.request({hostname:'127.0.0.1',port,path:route,method,signal,timeout:30000,
   headers:{'Content-Type':'application/json',...(body===undefined?{}:{'Content-Length':Buffer.byteLength(body)})}},res=>{
   const parts=[];let size=0;res.on('data',b=>{size+=b.length;if(size>2100000)res.destroy(Error('Response too large'));else parts.push(b);});
   res.on('error',reject);res.on('end',()=>resolve({status:res.statusCode,body:Buffer.concat(parts).toString('utf8')}));
  });req.on('timeout',()=>req.destroy(Error('Timeout')));req.on('error',reject);req.end(body);
 });
}
async function cdp(url){
 const u=new URL(url);if(u.protocol!=='ws:'||!['127.0.0.1','localhost'].includes(u.hostname))throw Error('CDP must be local');
 const ws=new WebSocket(url),pending=new Map(),events=[];let counter=0,handler=()=>{};
 ws.addEventListener('message',e=>{let v;try{v=JSON.parse(e.data)}catch{return}if(pending.has(v.id)){pending.get(v.id)(v);pending.delete(v.id);}else{if(v.method==='Runtime.executionContextCreated'){events.push(v);if(events.length>100)events.shift();}handler(v);}});
 await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{ws.close();reject(Error('CDP connect timeout'));},8000);ws.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('CDP connect failed'));},{once:true});});
 return {events,get alive(){return ws.readyState===WebSocket.OPEN},onEvent(fn){handler=fn;},close(){ws.close();},
  call(method,params={}){return new Promise((resolve,reject)=>{const id=++counter,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout'));},8000);pending.set(id,r=>{clearTimeout(timer);r.error?reject(Error('CDP operation rejected')):resolve(r.result)});ws.send(JSON.stringify({id,method,params}));});}};
}
async function evaluate(connection,expression,extra={}){
 const r=await connection.call('Runtime.evaluate',{expression,returnByValue:true,...extra});if(r.exceptionDetails)throw Error('Extension operation failed');return r.result?.value;
}
function normalizeName(v){return String(v).replace(/\s/g,'');}
function selectApplicationShell(targets){
 const hosts=targets.filter(t=>t.type==='page'&&typeof t.url==='string'&&t.url.startsWith('file:')&&/FACET/i.test(t.title||''));
 if(hosts.length!==1)throw Error('FACET application shell is missing or ambiguous');
 return hosts[0];
}
async function locate(cdpPort,instanceName){
 const raw=await direct(cdpPort,'/json/list');if(raw.status!==200)throw Error('CDP not ready');const targets=JSON.parse(raw.body);
 const selectedHost=selectApplicationShell(targets);
 const host=await cdp(selectedHost.webSocketDebuggerUrl);let instance;
 try{instance=await evaluate(host,`(()=>{if(typeof state!=='object'||!Array.isArray(state.instances))return null;const rows=state.instances.filter(i=>String(i.name).replace(/\\s/g,'')===${JSON.stringify(normalizeName(instanceName))});return rows.length===1?{url:rows[0].currentURL,name:rows[0].name,id:rows[0].id}:null})()`);}finally{host.close();}
 if(!instance||normalizeName(instance.name)!==normalizeName(instanceName))throw Error('Selected instance not found or ambiguous');
 const pages=targets.filter(t=>t.type==='page'&&t.url===instance.url&&/^https:\/\/arena\.ai(?:\/|$)/.test(t.url));
 if(pages.length!==1)throw Error('Selected conversation target not unique');
 const page=await cdp(pages[0].webSocketDebuggerUrl);
 try{
  const info=await page.call('Target.getTargetInfo');if(!info.targetInfo.browserContextId)throw Error('Instance context unavailable');
  await page.call('Runtime.enable');const worlds=page.events.filter(e=>e.method==='Runtime.executionContextCreated').map(e=>e.params.context);
  const own=worlds.filter(w=>w.name==='Arena2API'&&w.origin.startsWith('chrome-extension://'));if(own.length!==1)throw Error('Refresh the selected page after loading Arena2API');
  const all=await page.call('Target.getTargets');
  const workers=all.targetInfos.filter(t=>t.type==='service_worker'&&t.browserContextId===info.targetInfo.browserContextId&&t.url.startsWith(own[0].origin+'/'));
  if(workers.length!==1)throw Error('Open Arena2API extension in selected instance first');
  const target=targets.find(t=>t.id===workers[0].targetId);if(!target)throw Error('Extension worker restarted');
  return {endpoint:target.webSocketDebuggerUrl,context:info.targetInfo.browserContextId,instanceId:instance.id,instanceName:instance.name};
 }finally{page.close();}
}
async function attach(scope,port){
 const lease=JSON.stringify({id:crypto.createHash('sha256').update(scope.instanceId).digest('hex'),name:scope.instanceName});
 const worker=await cdp(scope.endpoint),inflight=new Map();let closed=false;
 try{
  const info=await worker.call('Target.getTargetInfo');if(info.targetInfo.browserContextId!==scope.context)throw Error('Instance context mismatch');
  if(await evaluate(worker,"chrome.runtime.getManifest().name")!=='Arena2API')throw Error('Extension identity mismatch');
  await worker.call('Runtime.enable');
  worker.onEvent(e=>{
   if(e.method!=='Runtime.bindingCalled'||e.params.name!=='__arena2apiLocalRequest')return;
   void(async()=>{
    let q;try{q=JSON.parse(e.params.payload);}catch{return}
    if(q.cancel){inflight.get(q.id)?.abort();return;}
    let result;
    try{
     const route=validateRequest(q,port);if(inflight.size>=16||inflight.has(q.id))throw Error('Busy');
     const abort=new AbortController();inflight.set(q.id,abort);result=await direct(port,route,q.method,q.body,abort.signal);
    }catch{result={error:true};}finally{inflight.delete(q?.id);}
    if(!closed&&typeof q?.id==='string')try{await evaluate(worker,`globalThis.Arena2APILocalTransport?.reply(${JSON.stringify(q.id)},${JSON.stringify(result)})`);}catch{}
   })();
  });
  await worker.call('Runtime.addBinding',{name:'__arena2apiLocalRequest'});
  const source=fs.readFileSync(path.join(ROOT,'extension/chrome/conversation/local-transport.js'),'utf8');
  await evaluate(worker,source+'\nglobalThis.Arena2APILocalTransport.renew('+port+','+lease+');');
  const health=await evaluate(worker,`(async()=>{const r=await fetch('http://127.0.0.1:${port}/health',{signal:AbortSignal.timeout(6000)});const j=await r.json();return {http:r.status,status:j.status}})()`,{awaitPromise:true});
  if(health?.http!==200||health.status!=='ok')throw Error('Local gateway health check failed');
  console.log('READY: selected instance Arena2API -> local gateway HTTP 200. No account request.');
  return {worker,async renew(){if(!worker.alive)throw Error('Worker disconnected');await evaluate(worker,'globalThis.Arena2APILocalTransport.renew('+port+','+lease+')');},async close(){closed=true;for(const c of inflight.values())c.abort();try{await evaluate(worker,'globalThis.Arena2APILocalTransport?.disable()');await worker.call('Runtime.removeBinding',{name:'__arena2apiLocalRequest'});}catch{}worker.close();}};
 }catch(e){worker.close();throw e;}
}
async function main(){
 const args=process.argv.slice(2),value=(name,fallback)=>{const i=args.indexOf(name);return i<0?fallback:args[i+1];};
 const cdpPort=Number(value('--cdp','36938')),name=value('--instance','实例 5'),seconds=Number(value('--seconds','0'));
 let config={};try{config=JSON.parse(fs.readFileSync(path.join(ROOT,'.console/config.json'),'utf8'));}catch{}
 const port=Number(value('--gateway',config.port||9090));
 if(![port,cdpPort].every(p=>Number.isInteger(p)&&p>=1024&&p<=65535)||!name||!Number.isFinite(seconds)||seconds<0)throw Error('Invalid bridge arguments');
 if(typeof WebSocket!=='function')throw Error('Node 22+ required');
 fs.mkdirSync(path.join(ROOT,'.console'),{recursive:true});const suffix=normalizeName(name)==='实例5'?'':('-'+crypto.createHash('sha256').update(normalizeName(name)+'|'+cdpPort).digest('hex').slice(0,12));const lock=path.join(ROOT,'.console/local-cdp-bridge'+suffix+'.lock');
 try{const old=JSON.parse(fs.readFileSync(lock,'utf8'));try{process.kill(old.pid,0);console.log('Bridge already running; no second bridge started.');return;}catch(e){if(e.code!=='ESRCH')throw Error('Existing bridge lock could not be verified');fs.unlinkSync(lock);}}catch(e){if(e.code!=='ENOENT')throw e;}
 fs.writeFileSync(lock,JSON.stringify({pid:process.pid}),{flag:'wx'});
 const statusFile=path.join(ROOT,'.console/local-cdp-bridge'+suffix+'-status.json');
 const status=ready=>fs.writeFileSync(statusFile,JSON.stringify({ready,protocol:2,pid:process.pid,instance:name,cdp_port:cdpPort,updated_at:Date.now(),gateway_port:port}));
 let stop=false,current=null,pinned=null;const started=Date.now();process.on('SIGINT',()=>{stop=true;});process.on('SIGTERM',()=>{stop=true;});
 console.log('Local-only conversation bridge. Keep this window open. Ctrl+C stops it.');
 try{
  while(!stop&&(!seconds||Date.now()-started<seconds*1000)){
   try{
    if(!current){const scope=await locate(cdpPort,name);if(pinned&&pinned!==scope.instanceId)throw Error('Instance identity changed');pinned=scope.instanceId;current=await attach(scope,port);status(true);}
    await current.renew();status(true);await new Promise(r=>setTimeout(r,3000));
   }catch(e){status(false);console.log('Bridge waiting: '+e.message);if(current){await current.close();current=null;}await new Promise(r=>setTimeout(r,3000));}
  }
 }finally{status(false);if(current)await current.close();try{const owner=JSON.parse(fs.readFileSync(lock,'utf8'));if(owner.pid===process.pid)fs.unlinkSync(lock);}catch{}}
}
module.exports={validateRequest,direct,normalizeName,selectApplicationShell,locate,attach};
if(require.main===module)main().catch(e=>{console.error('Bridge stopped: '+e.message);process.exitCode=1;});
