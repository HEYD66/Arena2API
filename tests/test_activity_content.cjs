/* Offline content -> extension worker -> result-route streaming integration. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111';
const source=n=>fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/',n),'utf8');
const tick=()=>new Promise(setImmediate),delay=ms=>new Promise(r=>setTimeout(r,ms));
const response=x=>new Response(JSON.stringify(x),{status:200,headers:{'Content-Type':'application/json'}});
function fixture({failProgress=false}={}) {
 let contentListener,workerListener,pollResolve,emit,emitEvent,complete,started=false;
 const calls=[],actions=[],progress=[],results=[];
 const timers=(fn,ms)=>{const t=setTimeout(fn,ms);t.unref();return t;};
 const core={LIMITS:{textChars:200000},ChatError:class extends Error{},contextFromUrl:url=>url===KEY?{origin:'https://arena.ai',key:KEY}:null,
  createClient:opts=>({send:async()=>{started=true;emitEvent=event=>opts.onEvent({type:'delta',event});emit=delta=>opts.onEvent({type:'delta',event:{type:'text-delta',delta}});await new Promise(r=>complete=r);},
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
  async start(stream=true,activity=true){
   for(let i=0;i<10&&!pollResolve;i++)await tick();assert(pollResolve);
   const common={account_key:'a'.repeat(64),source_session:calls.find(x=>x.url.endsWith('/offer')).body.session,context_key:KEY,expires_at:Date.now()+30000};
   for(const kind of ['catalog_list','catalog_select']){
    const send=pollResolve;pollResolve=null;send(response({...common,request_id:kind,kind,target:KEY}));
    for(let i=0;i<30&&!pollResolve;i++)await tick();assert(pollResolve);
   }
   results.length=0;
   pollResolve(response({...common,request_id:'fixture-request',kind:'send',text:'fixture',stream,activity_stream:activity}));
   for(let i=0;i<20&&!started;i++)await tick();assert(started);
  },
  emit:delta=>emit(delta),emitEvent:event=>emitEvent(event),complete:()=>complete(),
  async done(){for(let i=0;i<100&&!results.length;i++)await delay(5);assert.equal(results.length,1);await tick();}
 };
}
(async()=>{
 let count=0,f=fixture();await f.start();
 // 用户确认的新边界：思考原文转发（kind=reasoning）；工具参数和返回内容仍不转发
 f.emitEvent({type:'reasoning-delta',delta:'THOUGHT_A'});await delay(80);
 assert.equal(f.progress[0].progress.kind,'reasoning');assert.equal(f.progress[0].progress.delta,'THOUGHT_A');assert.equal(f.results.length,0);count++;
 for(let i=0;i<100;i++)f.emitEvent({type:'reasoning-delta',delta:'b'});
 await delay(80);const thought=f.progress.filter(x=>x.progress.kind==='reasoning').map(x=>x.progress.delta).join('');
 assert.equal(thought,'THOUGHT_A'+'b'.repeat(100));assert(f.progress.length<=3,'思考原文应按块合并转发');count++;
 f.emitEvent({type:'tool-input-available',toolName:'read_file',toolCallId:'opaque-id',input:{password:'SECRET_ARGUMENT'}});
 f.emitEvent({type:'tool-output-available',toolCallId:'opaque-id',output:'SECRET_OUTPUT'});
 f.emit('hello😀');f.complete();await f.done();
 assert(!JSON.stringify(f.progress).includes('SECRET'));assert(!JSON.stringify(f.progress).includes('opaque-id'));count++;
 const activities=f.progress.filter(x=>x.progress.kind==='activity');assert(activities.some(x=>x.progress.delta==='tool_returned'&&x.progress.tool==='read_file'));count++;
 assert.equal(f.results[0].result.text,'hello😀');assert.equal(f.progress.filter(x=>!x.progress.kind).map(x=>x.progress.delta).join(''),'hello😀');count++;
 assert.deepEqual(f.progress.map(x=>x.progress.seq),f.progress.map((_,i)=>i+1));assert.equal(f.actions.length,1);count++;
 f=fixture();await f.start();const msg={type:'CONVERSATION_PROGRESS',request_id:'fixture-request',context_key:KEY,progress:{seq:1,kind:'activity',delta:'thinking'}};
 assert.equal((await f.message(msg,{tab:{id:6}})).ok,false);count++;
 assert.equal((await f.message({...msg,progress:{...msg.progress,delta:'SECRET'}},{tab:{id:5}})).ok,false);count++;
 assert.equal((await f.message({...msg,progress:{...msg.progress,tool:'<tool_call>'}},{tab:{id:5}})).ok,false);count++;
 for(let i=0;i<400;i++)f.emitEvent({type:'tool-input-start',toolName:'fixture'+i,toolCallId:String(i)});
 f.emit('bounded');f.complete();await f.done();assert.equal(f.progress.filter(x=>x.progress.kind==='activity').length,256);assert.equal(f.results[0].result.text,'bounded');count++;
 f=fixture();await f.start(true,false);f.emitEvent({type:'reasoning-delta',delta:'private'});await delay(20);assert.equal(f.progress.length,0);
 assert.equal((await f.message(msg,{tab:{id:5}})).ok,false);f.emit('legacy');f.complete();await f.done();assert.equal(f.progress.length,1);count++;
 f=fixture({failProgress:true});await f.start();f.emitEvent({type:'reasoning-start'});await delay(30);f.emit('full result');f.complete();await f.done();assert.equal(f.results[0].result.text,'full result');assert.equal(f.actions.length,1);count++;
 console.log(count+' activity content/worker tests PASS; private text/arguments/output absent, scopes and limits retained');
})().catch(e=>{console.error(e);process.exitCode=1;});
