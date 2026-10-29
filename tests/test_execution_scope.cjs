const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const Core=require('../extension/chrome/conversation/chat-core.js');
const KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111';
const ID=KEY.split('/').at(-1), OTHER=KEY.replaceAll('11111111','22222222');
let checks=0;
function fixture(mode='ok', events=[], guardVersion=1){
 let outCount=0, clockAdvance=()=>{};
 let listener;const requests=[],location={href:KEY};let appended=0;
 const fetch=async(url,init={})=>{
  requests.push({url,method:init.method||'GET',body:init.body});
  const json=x=>new Response(JSON.stringify(x),{status:200,headers:{'Content-Type':'application/json'}});
  if(url.includes('/api/chat/'+ID+'?'))return json({id:ID,taskId:'chat-agent',lastEventId:'10',title:'fixture',isStreaming:mode==='busy',messages:[{id:'old',role:'assistant',parts:[{type:'text',text:'history'},{type:'tool-private',input:{secret:'must-not-cross'}}]}]});
  if(url.endsWith('/chat/trigger-session'))return json({publicAccessToken:'fixture-token'});
  if(url.endsWith('/in/append')){
   appended++;const b=JSON.parse(init.body);assert.equal(b.payload.chatId,ID);assert.equal(b.kind,'message');
   if(mode==='unknown')throw Error('fixture transport loss');return json({ok:true,seq:4});
  }
  if(url.endsWith('/out')){
   outCount++;clockAdvance();
   const records=[...events.map((e,i)=>({seq_num:11+i,headers:[['session-in-event-id','4']],body:JSON.stringify({data:e})})),{seq_num:11+events.length,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'text-delta',delta:'hello'}})},
    {seq_num:12+events.length,headers:[['trigger-control','turn-complete'],['session-in-event-id','4']],body:''}];
   if(mode==='leases' && outCount<27)records.pop();
   return new Response('event: batch\ndata: '+JSON.stringify({records})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
  }
  throw Error('Unexpected endpoint: '+url);
 };
 const sandbox={ArenaFusionChat:{...Core,CLIENT_EXECUTION_GUARD:guardVersion,createClient:o=>Core.createClient({...o,timeouts:{...o.timeouts,reconnectBase:1,reconnectMax:2},fetch,crypto:require('node:crypto').webcrypto})},location,
  document:{title:'fixture'},chrome:{runtime:{onMessage:{addListener:f=>listener=f}}},setInterval:()=>0,addEventListener:()=>{},Date,Map,Error};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/content.js'),'utf8'),sandbox);
 async function run(kind,id='command-'+Math.random(),key=KEY,expiry=Date.now()+30000,text='hello', flags={}){
  const r=await new Promise(resolve=>listener({type:'CONVERSATION_ACTION',command:{kind,request_id:id,context_key:key,expires_at:expiry,text,...flags}},{},resolve));
  await new Promise(setImmediate);return r;
 }
 return {run,requests,location,setClock:f=>clockAdvance=f,get outCount(){return outCount;},get appended(){return appended;},context:()=>new Promise(r=>listener({type:'CONVERSATION_CONTEXT'},{},r))};
}

const flags={client_execution:true,reference_reads:true};
const send=(f, opts=flags)=>f.run('send',undefined,KEY,Date.now()+30000,'fixture',opts);
const input=(toolName,input)=>({type:'tool-input-available',toolCallId:'native-1',toolName,input});
(async()=>{
 let f=fixture();let r=await send(f);assert.equal(r.execution_scope_verified,true);checks++;
 for(const name of ['write_file','present_file','edit_file','web_search','ask_user']){
  f=fixture('ok',[input(name,{path:'/home/user/project.html'})]);r=await send(f);
  assert.equal(r.error_code,'EXECUTION_SCOPE');assert.equal(r.uncertain,true);assert.equal(f.appended,1);assert.equal(f.outCount,1);
  await send(f);assert.equal(f.appended,1);checks++;
 }
 for(const a of [{path:'/home/user/project.html',offset:1,limit:10},{path:'/home/user/uploads/tools-reference.md',offset:1,limit:1000},{path:'/home/user/uploads/../project.html',offset:1,limit:10},{path:'/home/user/uploads/tools-index.md',offset:1}]){
  f=fixture('ok',[input('read_file',a)]);assert.equal((await send(f)).error_code,'EXECUTION_SCOPE');checks++;
 }
 for(const name of ['read_file','functions.read_file']){
  f=fixture('ok',[input(name,{path:'/home/user/uploads/tools-index.md',offset:1,limit:300}),{type:'tool-output-available',toolCallId:'native-1',output:'reference'}]);
  assert.equal((await send(f)).ok,true);checks++;
  assert.equal((await send(f,{client_execution:true,reference_reads:false})).error_code,'EXECUTION_SCOPE');checks++;
 }
 const hash='sha256sum /home/user/uploads/tools-index.md /home/user/uploads/tools-reference.md';
 f=fixture('ok',[input('bash',{command:hash,cwd:'/home/user',timeout:30})]);assert.equal((await send(f)).ok,true);checks++;
 for(const command of [hash+'; touch x','cat /home/user/uploads/tools-index.md','echo test']){
  f=fixture('ok',[input('bash',{command})]);assert.equal((await send(f)).error_code,'EXECUTION_SCOPE');checks++;
 }
 f=fixture('ok',[{type:'tool-output-available',toolCallId:'unknown',output:'artifact'}]);assert.equal((await send(f)).error_code,'EXECUTION_SCOPE');checks++;
 f=fixture('ok',[],undefined); // explicit older core feature flag
 f=fixture('ok',[],0);assert.equal((await send(f)).error_code,'CLIENT_SCOPE_UNVERIFIED');assert.equal(f.appended,0);checks++;
 const now=Date.now;let advanced=0;Date.now=()=>now()+advanced;
 try {f=fixture('leases');f.setClock(()=>advanced+=55000);r=await send(f);assert.equal(r.ok,true);assert.equal(f.outCount,27);assert.equal(f.appended,1);assert.equal(r.text,'hello');checks++;}
 finally {Date.now=now;}
 // A throwing observer is swallowed; enforcement must happen in the parser, not that observer.
 const filter=new Core.TurnFilter({seq:4,lastEventId:'10',knownIds:[],onEvent:()=>{},nativeToolPolicy:()=>false});
 assert.throws(()=>filter.frame({event:'batch',data:JSON.stringify({records:[{seq_num:11,headers:[['session-in-event-id','4']],body:JSON.stringify({data:input('write_file',{})})}]})}),e=>e.code==='EXECUTION_SCOPE');checks++;
 f=fixture('ok',[{type:'tool-input-start',toolName:'bash',toolCallId:'incomplete'}]);r=await send(f);assert.equal(r.error_code,'EXECUTION_SCOPE');assert.equal(r.uncertain,true);checks++;
 let inspected=0;const old=new Core.TurnFilter({seq:4,lastEventId:null,onEvent:()=>{},nativeToolPolicy:()=>{inspected++;return false;}});
 old.frame({event:'batch',data:JSON.stringify({records:[{seq_num:1,headers:[],body:JSON.stringify({data:input('write_file',{})})},{seq_num:2,headers:[['trigger-control','turn-complete'],['session-in-event-id','3']],body:''}]})});
 assert.equal(inspected,0);checks++;
 console.log(checks+' execution-scope/long-receive checks PASS; simulated 1485 seconds, 27 leases, no real sends');
})().catch(e=>{console.error(e);process.exitCode=1;});
