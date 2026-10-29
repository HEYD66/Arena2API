if(!process.env.DSH_NODE_MODULES) throw new Error('Set DSH_NODE_MODULES to the installed DSH resources/app/node_modules directory.');
// Isolated actual installed adapter, synthetic provider only. No account, gateway or tool calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const root=(process.env.DSH_NODE_MODULES||'').replace(/[\/]?$/, '/')+'@deepseek-ai/dsh-llm-pi-ai/lib/index.js';
const {PiAiAdapter}=await import(pathToFileURL(root));
const baseline=process.argv.includes('--baseline');
const originalFetch=globalThis.fetch;
globalThis.fetch=()=>{throw Error('NETWORK_FORBIDDEN_IN_FIXTURE');};
const realSet=setTimeout,realClear=clearTimeout;
let now=0,nextId=0,timers=new Map();
globalThis.setTimeout=(fn,ms=0,...args)=>{const id=++nextId;timers.set(id,{at:now+ms,fn:()=>fn(...args)});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
async function flush(){for(let i=0;i<100;i++)await Promise.resolve();}
async function advance(ms){const end=now+ms;for(;;){await flush();const due=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].fn();}now=end;await flush();}
function delay(ms,signal){return new Promise((resolve,reject)=>{let id;const abort=()=>{clearTimeout(id);signal.removeEventListener('abort',abort);reject(Error('fixture abort'));};id=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});});}
function run({url='http://127.0.0.1:9090/v1',id='opus5.5',api='openai-completions',wait=385000,idle=300000,requestTimeout,signal}={}){
 const profile=Object.freeze({streamIdleTimeoutMs:idle,timeoutMs:requestTimeout,modelErrors:new Map(),configuredMaxTokens:new Map(),piProvider:{},headers:Object.freeze({})});
 const model={id,name:id,api,provider:'fixture',baseUrl:url,input:['text'],reasoning:false,contextWindow:128000,maxTokens:8192};
 let calls=0,opts;const message={role:'assistant',content:[{type:'text',text:'fixture ok'}],api,provider:'fixture',model:id,stopReason:'stop',timestamp:0,usage:{input:1,output:1,totalTokens:2,cacheRead:0,cacheWrite:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
 const snapshot={profiles:new Map([['fixture',profile]]),models:{getModel:()=>model,async *streamSimple(m,c,o){calls++;opts=o;yield {type:'start',partial:message};await delay(wait,o.signal);yield {type:'text_delta',contentIndex:0,delta:'fixture ok'};yield {type:'done',message};}}};
 const adapter=new PiAiAdapter({resolveApiKey:async()=>undefined});
 const chunks=[],result={state:'pending'};
 const task=(async()=>{try{for await(const c of adapter.streamWithSnapshot({provider:'fixture',model:id,messages:[],signal},snapshot))chunks.push(c);result.state='done';}catch(e){result.state='error';result.code=e.code;result.message=e.message;}})();
 return {task,result,chunks,profile,get calls(){return calls;},get opts(){return opts;}};
}
let count=0;
try{
 let f=run();await flush();assert.equal(f.calls,1);await advance(300000);
 if(baseline){assert.equal(f.result.state,'error');assert.equal(f.result.code,'TIMEOUT');assert.match(f.result.message,/300000ms/);assert.equal(f.chunks.length,0);await f.task;count++;console.log('BASELINE_REPRODUCED: installed adapter aborts at 300000ms while gateway can still hold tool JSON; one synthetic request.');}
 else{
  assert.equal(f.result.state,'pending');assert.equal(f.opts.timeoutMs,undefined);assert.equal(f.profile.streamIdleTimeoutMs,300000);assert.equal(f.profile.timeoutMs,undefined);count++;
  await advance(85000);await f.task;assert.equal(f.result.state,'done');assert(f.chunks.some(c=>c.type==='text-delta'));assert.equal(f.calls,1);count++;
  for(const changes of [{url:'http://localhost:9090/v1/'},{url:'http://127.0.0.1:9090/v1/'}]){f=run(changes);await advance(385000);await f.task;assert.equal(f.result.state,'done');assert.equal(f.opts.timeoutMs,undefined);count++;}
  for(const changes of [{id:'other'},{api:'openai-responses'},{url:'https://example.com/v1'},{url:'http://127.0.0.1:9999/v1'},{url:'http://127.0.0.1:9090/v1/other'},{url:'http://127.0.0.1:9090/v1?q=1'},{url:'http://user@127.0.0.1:9090/v1'},{url:'http://localhost.example.com:9090/v1'},{url:'not a URL'}]){
   f=run(changes);await advance(300000);await f.task;assert.equal(f.result.code,'TIMEOUT');assert.equal(f.opts.timeoutMs,undefined);assert.equal(f.calls,1);count++;
  }
  f=run({wait:14400000});await advance(1000000);assert.equal(f.result.state,'pending');await advance(13400000);await f.task;assert.equal(f.result.state,'done');assert.equal(f.calls,1);count++;
  const c=new AbortController();f=run({signal:c.signal});await flush();c.abort('user cancelled');await flush();await f.task;assert.equal(f.result.code,'ABORTED');assert.equal(f.calls,1);count++;
  const pre=new AbortController();pre.abort('already cancelled');f=run({signal:pre.signal});await flush();await f.task;assert.equal(f.result.code,'ABORTED');count++;
  f=run({idle:1500000,requestTimeout:2000000});await advance(385000);await f.task;assert.equal(f.result.state,'done');assert.equal(f.opts.timeoutMs,2000000);count++;
  const scoped=run(),remote=run({url:'https://example.com/v1'});await advance(300000);assert.equal(remote.result.code,'TIMEOUT');assert.equal(scoped.result.state,'pending');await advance(85000);await Promise.all([scoped.task,remote.task]);assert.equal(scoped.result.state,'done');count++;
  assert.equal(timers.size,0);count++;
  console.log(count+' installed-adapter checks PASS; fake clock 4 hours success, no model-content idle deadline, cancellation, routing isolation; zero network/tool calls.');
 }
 assert.equal(timers.size,0);
}finally{globalThis.setTimeout=realSet;globalThis.clearTimeout=realClear;globalThis.fetch=originalFetch;}
