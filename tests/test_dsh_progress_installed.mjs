if(!process.env.DSH_NODE_MODULES) throw new Error('Set DSH_NODE_MODULES to the installed DSH resources/app/node_modules directory.');
// Actual installed DSH plugin + pi-ai/OpenAI SSE decoder; synthetic fetch only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const root=(process.env.DSH_NODE_MODULES||'').replace(/[\/]?$/, '/');
const wire=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
let requests=0, controller, requestSignal;
const originalFetch=globalThis.fetch;
const mockFetch=async(url,init)=>{
 assert.equal(String(url),'http://127.0.0.1:9090/v1/chat/completions');requests++;
 requestSignal=init.signal;
 const body=new ReadableStream({start(c){controller=c;for(const f of wire.initial)c.enqueue(new TextEncoder().encode(f));}});
 return new Response(body,{headers:{'content-type':'text/event-stream'},status:200});
};
globalThis.fetch=mockFetch;
try {
 const {PiAiAdapter}=await import(pathToFileURL(root+'@deepseek-ai/dsh-llm-pi-ai/lib/index.js'));
 const api=await import(pathToFileURL(root+'@earendil-works/pi-ai/dist/api/openai-completions.js'));
 const model={id:'opus5.5',provider:'fixture',name:'fixture',api:'openai-completions',baseUrl:'http://127.0.0.1:9090/v1',input:['text'],reasoning:false,contextWindow:128000,maxTokens:8192,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
 const profile=Object.freeze({streamIdleTimeoutMs:300000,modelErrors:new Map(),configuredMaxTokens:new Map(),piProvider:{},headers:{}});
 const snapshot={profiles:new Map([['fixture',profile]]),models:{getModel:()=>model,streamSimple:(m,c,o)=>api.streamSimple(m,c,{...o,fetch:mockFetch})}};
 const adapter=new PiAiAdapter({resolveApiKey:async()=> 'synthetic-fixture-key'});
 let readyResolve,readyReject;const ready=new Promise((r,j)=>{readyResolve=r;readyReject=j;});const chunks=[];
 const task=(async()=>{try{for await(const c of adapter.streamWithSnapshot({provider:'fixture',model:'opus5.5',messages:[]},snapshot)){chunks.push(c);if(c.type==='reasoning-delta')readyResolve();}}catch(e){readyReject(e);throw e;}})();
 const timer=setTimeout(()=>readyReject(Error('No live reasoning chunk from real installed adapter')),5000);
 try {await ready;} finally {clearTimeout(timer);}
 assert.equal(requests,1);assert(chunks.some(c=>c.type==='block-start'&&c.blockType==='reasoning'));assert(chunks.some(c=>c.type==='reasoning-delta'&&c.text.includes('网页工具')));
 assert(!chunks.some(c=>c.blockType==='tool-call'||c.type==='finish'));assert.equal(requestSignal.aborted,false);
 for(const frame of wire.final)controller.enqueue(new TextEncoder().encode(frame));controller.close();await task;
 assert(chunks.some(c=>c.type==='finish'&&c.reason.kind==='tool-calls'));
 assert.equal(chunks.filter(c=>c.type==='block-end'&&c.block?.type==='tool-call').length,1);
 assert.equal(chunks.find(c=>c.type==='block-end'&&c.block?.type==='tool-call').block.arguments,'{"x":1}');
 assert.equal(profile.streamIdleTimeoutMs,300000);assert.equal(requests,1);
 console.log('PASS: real gateway SSE fixture -> installed pi-ai/OpenAI parser -> installed DSH reasoning block, visible before final; exactly one validated tool-call block; no network/tool execution.');
} finally {globalThis.fetch=originalFetch;}
