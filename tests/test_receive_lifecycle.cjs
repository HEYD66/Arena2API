// Offline lifecycle regressions: elapsed time, prolonged silence, transport recovery, cancellation.
'use strict';
const assert=require('node:assert/strict'),Core=require('../extension/chrome/conversation/chat-core.js');
const ID='11111111-1111-4111-8111-111111111111',KEY='https://arena.ai/agent/'+ID;
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
const enc=x=>new TextEncoder().encode(x),direct=x=>enc('data: '+JSON.stringify(x)+'\n\n');
const batch=records=>enc('event: batch\ndata: '+JSON.stringify({records})+'\n\n');
const record=(n,text)=>({seq_num:n,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'text-delta',delta:text}})});
const end=n=>({seq_num:n,headers:[['trigger-control','turn-complete'],['session-in-event-id','4']],body:''});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function fixture(mode){
 let gets=0,posts=0,reads=0,pendingRead,cancels=0,context=Core.contextFromUrl(KEY),advance=()=>{};
 const events=[];
 const client=Core.createClient({getContext:()=>context,crypto:require('node:crypto').webcrypto,timeouts:{request:50,streamIdle:10,streamTotal:1,reconnectBase:1,reconnectMax:2},onEvent:e=>events.push(e),fetch:async(url,init={})=>{
  if(url.includes('/api/chat/'+ID+'?'))return json({id:ID,taskId:'chat-agent',lastEventId:'10',messages:[]});
  if(url.endsWith('/chat/trigger-session'))return json({publicAccessToken:'fixture-only'});
  if(url.endsWith('/in/append')){posts++;return json({seq:4});}
  assert(url.endsWith('/out'));gets++;
  if(mode==='connect-recovery'&&gets<9)throw TypeError('network fixture');
  if(mode==='connect-recovery')return new Response(batch([record(11,'ok'),end(12)]),{headers:{'Content-Type':'text/event-stream','X-Stream-Version':'v2'}});
  if(mode==='backoff')throw TypeError('network fixture');
  let n=0;const legacy=mode==='quiet-legacy'||mode==='context';
  return {ok:true,headers:new Headers({'Content-Type':'text/event-stream','X-Stream-Version':legacy?'v1':'v2'}),body:{getReader(){return {
   read(){reads++;advance();n++;
    if(mode==='active-long')return Promise.resolve({done:false,value:batch(n<=24?[record(n+10,'x')]:[end(35)])});
    if(n===1)return Promise.resolve({done:false,value:legacy?direct({type:'text-delta',delta:'first'}):batch([record(11,'first')])});
    return new Promise(resolve=>pendingRead=resolve);
   },cancel(){cancels++;pendingRead?.({done:true});return Promise.resolve();},releaseLock(){}
  }}}};
 }});
 return {client,events,get gets(){return gets},get posts(){return posts},get reads(){return reads},get cancels(){return cancels},setAdvance:f=>advance=f,changeContext:()=>context=Core.contextFromUrl(KEY.replace('11111111','22222222')),finishLegacy:()=>pendingRead({done:false,value:direct({type:'trigger:turn-complete',sessionInEventId:4})})};
}
(async()=>{
 let count=0;
 const realNow=Date.now;let offset=0;Date.now=()=>realNow()+offset;
 try{const f=fixture('active-long');f.setAdvance(()=>offset+=3600000);await f.client.send({text:'fixture'});assert.equal(f.posts,1);assert.equal(f.gets,1);assert.equal(f.events.filter(e=>e.type==='delta').map(e=>e.event.delta).join(''),'x'.repeat(24));assert.equal(f.client.getState().pending,false);count++;}finally{Date.now=realNow;}
 let f=fixture('quiet-legacy'),done=false;let task=f.client.send({text:'fixture'}).then(()=>done=true);
 await sleep(65);assert(!done);assert.equal(f.gets,1);assert.equal(f.reads,2);assert.equal(f.cancels,0);assert(f.events.filter(e=>e.type==='state'&&e.text.includes('保留原连接')).length>=2);f.finishLegacy();await task;assert.equal(f.posts,1);count++;
 f=fixture('connect-recovery');await f.client.send({text:'fixture'});assert.equal(f.gets,9);assert.equal(f.posts,1);count++;
 f=fixture('backoff');task=f.client.send({text:'fixture'});const rejected=assert.rejects(task,e=>e.code==='CANCELLED');await sleep(20);assert(f.gets>=2);f.client.dispose();await rejected;const last=f.gets;await sleep(15);assert.equal(f.gets,last);assert.equal(f.posts,1);count++;
 f=fixture('quiet-legacy');task=f.client.send({text:'fixture'});const stopped=assert.rejects(task,e=>e.code==='CANCELLED');await sleep(15);f.client.dispose();await stopped;assert.equal(f.gets,1);count++;
 f=fixture('context');task=f.client.send({text:'fixture'});const changed=assert.rejects(task,e=>e.code==='CONTEXT_CHANGED');await sleep(15);f.changeContext();await changed;assert.equal(f.gets,1);assert.equal(f.posts,1);count++;
 console.log(count+' receive lifecycle checks PASS: simulated 25 hours active, quiet same read, 9 connects, cancel/backoff/context; one append; zero network');
})().catch(e=>{console.error(e);process.exitCode=1});
