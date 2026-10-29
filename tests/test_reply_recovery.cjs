'use strict';
const assert=require('node:assert/strict');
const Core=require('../extension/chrome/conversation/chat-core.js');
const ID='11111111-1111-4111-8111-111111111111',KEY='https://arena.ai/agent/'+ID;
const text='<tool_call>{"name":"pwsh","arguments":{"command":"Write-Output 63"}}</tool_call>';
const record=(seq,delta)=>({seq_num:seq,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'text-delta',delta}})});
const first=record(11,text.slice(0,23)),last=record(12,text.slice(23));
const finish={seq_num:13,headers:[['trigger-control','turn-complete'],['session-in-event-id','4']],body:''};
const encode=records=>new TextEncoder().encode('event: batch\ndata: '+JSON.stringify({records})+'\n\n');
function fixture(mode){
 let gets=0,posts=0,context=Core.contextFromUrl(KEY);const cursors=[],deltas=[];
 const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
 const client=Core.createClient({getContext:()=>context,crypto:require('node:crypto').webcrypto,timeouts:{streamIdle:25,streamTotal:1,reconnectBase:1,reconnectMax:2},onEvent:e=>{if(e.type==='delta'&&e.event.type==='text-delta')deltas.push(e.event.delta)},fetch:async(url,init={})=>{
  if(url.includes('/api/chat/'+ID+'?'))return json({id:ID,taskId:'chat-agent',lastEventId:mode==='no-cursor'?null:'10',messages:[]});
  if(url.endsWith('/chat/trigger-session'))return json({publicAccessToken:'PRIVATE-FIXTURE-TOKEN'});
  if(url.endsWith('/in/append')){posts++;return json({seq:4});}
  if(!url.endsWith('/out'))throw Error('Unexpected endpoint');
  gets++;cursors.push(init.headers['Last-Event-ID']);
  if(mode==='http')return new Response('',{status:403});
  if(gets>1&&mode==='connect'&&gets===2)throw TypeError('PRIVATE-FIXTURE-TOKEN');
  if(gets>1&&(!['permanent','eof-unconfirmed'].includes(mode)||gets>=8))return new Response(encode([first,last,finish]),{headers:{'Content-Type':'text/event-stream','X-Stream-Version':'v2'}});
  let n=0;
  return {ok:true,headers:new Headers({'Content-Type':'text/event-stream','X-Stream-Version':mode==='direct'?'v1':'v2'}),body:{getReader(){return {
   async read(){if(n++===0){
    if(mode==='parser')return {value:new TextEncoder().encode('event: batch\ndata: {bad}\n\n'),done:false};
    if(mode==='direct')return {value:new TextEncoder().encode('id: 11\ndata: '+JSON.stringify({type:'text-delta',delta:'part'})+'\n\n'),done:false};
    if(mode==='wrong-turn')return {value:encode([{...finish,headers:[['trigger-control','turn-complete'],['session-in-event-id','5']]}]),done:false};
    return {value:encode([mode==='no-cursor'?{headers:first.headers,body:first.body}:first]),done:false};
   }
   if(mode==='context')context=Core.contextFromUrl(KEY.replace('11111111','22222222'));
   if(mode==='timeout')return new Promise(()=>{});
   if(mode==='eof'||mode==='eof-unconfirmed')return {done:true};
   throw TypeError('PRIVATE-FIXTURE-TOKEN https://private.invalid');
   },cancel(){return Promise.resolve()},releaseLock(){if(mode==='cleanup')throw Error('PRIVATE-FIXTURE-TOKEN')}
  }}}};
 }});
 return {client,deltas,cursors,get gets(){return gets},get posts(){return posts}};
}
(async()=>{
 let checks=0;
 for(const mode of ['read','connect','timeout','eof','cleanup','permanent','eof-unconfirmed']){
  const f=fixture(mode);await f.client.send({text:'fixture'});assert.equal(f.posts,1);assert.equal(f.deltas.join(''),text);assert.equal(f.client.getState().pending,false);assert(f.gets>=2&&f.gets<=8);assert(f.cursors.slice(1).every(c=>c==='11'));checks++;
 }
 for(const [mode,code,count] of [['parser','STREAM_PROTOCOL',1],['http','ACCESS_DENIED',1],['direct','STREAM_READ',1],['no-cursor','STREAM_READ',1],['wrong-turn','OTHER_INPUT',1],['context','CONTEXT_CHANGED',1]]){
  const f=fixture(mode);await assert.rejects(f.client.send({text:'fixture'}),e=>{assert.equal(e.code,code);assert(!e.message.includes('PRIVATE-FIXTURE-TOKEN'));return true});assert.equal(f.gets,count);assert.equal(f.posts,1);assert.equal(f.client.getState().pending,true);await assert.rejects(f.client.send({text:'duplicate'}),e=>e.code==='BUSY');assert.equal(f.posts,1);checks++;
 }
 console.log(checks+' reply recovery cases PASS; mocked network only, one append, cursor deduplication and failure fences preserved');
})().catch(e=>{console.error(e);process.exitCode=1});
