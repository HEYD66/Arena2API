/* Offline content -> extension worker -> result-route streaming integration. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111';
const source=n=>fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/',n),'utf8');
const tick=()=>new Promise(setImmediate),delay=ms=>new Promise(r=>setTimeout(r,ms));
const response=x=>new Response(JSON.stringify(x),{status:200,headers:{'Content-Type':'application/json'}});
function fixture({failProgress=false}={}) {
 let contentListener,workerListener,pollResolve,emit,complete,started=false;
 const calls=[],actions=[],progress=[],results=[];
 const timers=(fn,ms)=>{const t=setTimeout(fn,ms);t.unref();return t;};
 const core={LIMITS:{textChars:200000},ChatError:class extends Error{},contextFromUrl:url=>url===KEY?{origin:'https://arena.ai',key:KEY}:null,
  createClient:opts=>({send:async()=>{started=true;emit=delta=>opts.onEvent({type:'delta',event:{type:'text-delta',delta}});await new Promise(r=>complete=r);},
   getState:()=>({pending:false}),cancelForNavigation(){},dispose(){}})};
 const content={ArenaFusionChat:core,location:{href:KEY},document:{title:'fixture'},Date,Map,Error,
  setTimeout:timers,clearTimeout,setInterval:()=>0,addEventListener:()=>{},
  chrome:{runtime:{onMessage:{addListener:f=>contentListener=f},sendMessage:(m,cb)=>workerListener(m,{tab:{id:5}},cb)}}};
 const worker={arenaAccountIdentity:()=>{},Arena2APILocalTransport:{source:()=>({id:'a'.repeat(64),name:'实例 5'}),endpoint:()=> 'http://127.0.0.1:9191'},crypto:require('node:crypto').webcrypto,URL,AbortController,Date,Number,String,Promise,Error,
  setTimeout:timers,clearTimeout,setInterval:()=>0,
  fetch:async(url,init)=>{
   const b=init.body?JSON.parse(init.body):null;calls.push({url,body:b});
   if(url.includes('/command?'))return new Promise(r=>pollResolve=r);
   if(url.endsWith('/result')){
    if(b.progress){progress.push(b);if(failProgress)throw Error('fixture progress delivery failed');}
    else results.push(b);
   }
   return response({ok:true});
  },
  chrome:{scripting:{executeScript:(opts,cb)=>cb([{result:{ok:true,account_key:'a'.repeat(64),label:'fixture'}}])},runtime:{onMessage:{addListener:f=>workerListener=f}},storage:{local:{get:(keys,cb)=>cb({}),set:(v,cb)=>cb()}},
   tabs:{query:(q,cb)=>cb([{id:5,url:KEY,title:'fixture',active:true}]),sendMessage:(tab,msg,cb)=>{if(msg.type==='CONVERSATION_CATALOG'){cb(msg.kind==='list'?{ok:true,rows:[{context_key:KEY,title:'fixture'}],cursor:null}:{ok:true,context_key:KEY,title:'fixture'});return;}if(msg.type==='CONVERSATION_ACTION')actions.push(msg.command);contentListener(msg,{},cb);}}}};
 vm.runInNewContext(source('content.js'),content);vm.runInNewContext(source('background.js'),worker);
 const message=(msg,sender={})=>new Promise(r=>workerListener(msg,sender,r));
 return {calls,actions,progress,results,message,
  async start(stream=true){
   for(let i=0;i<10&&!pollResolve;i++)await tick();assert(pollResolve);
   const common={account_key:'a'.repeat(64),source_session:calls.find(x=>x.url.endsWith('/offer')).body.session,context_key:KEY,expires_at:Date.now()+30000};
   for(const kind of ['catalog_list','catalog_select']){
    const send=pollResolve;pollResolve=null;send(response({...common,request_id:kind,kind,target:KEY}));
    for(let i=0;i<30&&!pollResolve;i++)await tick();assert(pollResolve);
   }
   results.length=0;
   pollResolve(response({...common,request_id:'fixture-request',kind:'send',text:'fixture',stream}));
   for(let i=0;i<20&&!started;i++)await tick();assert(started);
  },
  emit:delta=>emit(delta),complete:()=>complete(),
  async done(){for(let i=0;i<100&&!results.length;i++)await delay(5);assert.equal(results.length,1);await tick();}
 };
}
(async()=>{
 let checks=0;
 let f=fixture();await f.start();f.emit('hello');await delay(80);
 assert.equal(f.progress.length,1);assert.equal(f.results.length,0);assert.equal(f.progress[0].progress.delta,'hello');checks++;
 assert.equal(f.progress[0].request_id,'fixture-request');assert.equal(f.progress[0].context_key,KEY);checks++;
 const wrong={type:'CONVERSATION_PROGRESS',request_id:'fixture-request',context_key:KEY,progress:{seq:2,delta:'foreign'}};
 assert.equal((await f.message(wrong,{tab:{id:6}})).ok,false);checks++;
 assert.equal((await f.message({...wrong,request_id:'wrong'},{tab:{id:5}})).ok,false);checks++;
 assert.equal((await f.message({...wrong,progress:{seq:2,delta:'x'.repeat(16001)}},{tab:{id:5}})).ok,false);checks++;
 f.emit(' world😀');f.complete();await f.done();assert.equal(f.results[0].result.text,'hello world😀');assert.equal(f.results[0].result.completed,true);checks++;
 assert.equal(f.progress.map(x=>x.progress.delta).join(''),'hello world😀');assert.deepEqual(f.progress.map(x=>x.progress.seq),[1,2]);checks++;
 assert.equal(f.actions.length,1);assert.equal((await f.message(wrong,{tab:{id:5}})).ok,false);checks++;
 assert(f.calls.every(x=>x.url.startsWith('http://127.0.0.1:9191/v1/conversation/')));checks++;
 f=fixture();await f.start(false);f.emit('nonstream');f.complete();await f.done();assert.equal(f.progress.length,0);assert.equal(f.results[0].result.text,'nonstream');checks++;
 f=fixture({failProgress:true});await f.start();f.emit('before');await delay(80);f.emit(' after');f.complete();await f.done();assert.equal(f.results[0].result.text,'before after');assert.equal(f.actions.length,1);checks++;
 f=fixture();await f.start();const text='x'.repeat(15999)+'😀'+'y'.repeat(19000);f.emit(text);f.complete();await f.done();
 assert(f.progress.every(x=>x.progress.delta.length<=16000 && !/[\uD800-\uDBFF]$/.test(x.progress.delta)));assert.equal(f.progress.map(x=>x.progress.delta).join(''),text);checks++;
 f=fixture();await f.start();f.emit('x'.repeat(1000001));f.complete();await f.done();assert.equal(f.results[0].result.ok,false);assert.notEqual(f.results[0].result.completed,true);checks++;
 console.log(checks+' real-time extension integration checks PASS; no network/account requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
