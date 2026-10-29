const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const Core=require('../extension/chrome/conversation/chat-core.js');
const KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111';
const ID=KEY.split('/').at(-1), OTHER=KEY.replaceAll('11111111','22222222');
let checks=0;
function fixture(mode='ok'){
 let listener;const requests=[],location={href:KEY};let appended=0;
 const fetch=async(url,init={})=>{
  requests.push({url,method:init.method||'GET',body:init.body});
  const json=x=>new Response(JSON.stringify(x),{status:200,headers:{'Content-Type':'application/json'}});
  if(url.includes('/api/chat/'+ID+'?'))return json({id:ID,taskId:'chat-agent',lastEventId:'10',title:'fixture',isStreaming:mode==='busy',messages:[{id:'old',role:'assistant',parts:[{type:'text',text:'history'},{type:'tool-private',input:{secret:'must-not-cross'}}]}]});
  if(url.endsWith('/chat/trigger-session')&&mode==='auth-verification')return new Response('{"error":"captcha required"}',{status:403});
  if(url.endsWith('/chat/trigger-session'))return json({publicAccessToken:'fixture-token'});
  if(url.endsWith('/in/append')){
   appended++;const b=JSON.parse(init.body);assert.equal(b.payload.chatId,ID);assert.equal(b.kind,'message');
   if(mode==='verification')return new Response('{"error":"recaptcha rejected"}',{status:403});
   if(mode==='unknown')throw Error('fixture transport loss');return json({ok:true,seq:4});
  }
  if(url.endsWith('/out')){
   const records=[{seq_num:11,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'text-delta',delta:'hello'}})},
    {seq_num:12,headers:[['trigger-control','turn-complete'],['session-in-event-id','4']],body:''}];
   return new Response('event: batch\ndata: '+JSON.stringify({records})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
  }
  throw Error('Unexpected endpoint: '+url);
 };
 const sandbox={ArenaFusionChat:{...Core,createClient:o=>Core.createClient({...o,fetch,crypto:require('node:crypto').webcrypto})},location,
  document:{title:'fixture'},chrome:{runtime:{onMessage:{addListener:f=>listener=f}}},setInterval:()=>0,addEventListener:()=>{},Date,Map,Error};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/content.js'),'utf8'),sandbox);
 async function run(kind,id='command-'+Math.random(),key=KEY,expiry=Date.now()+30000,text='hello'){
  const r=await new Promise(resolve=>listener({type:'CONVERSATION_ACTION',command:{kind,request_id:id,context_key:key,expires_at:expiry,text}},{},resolve));
  await new Promise(setImmediate);return r;
 }
 return {run,requests,location,get appended(){return appended;},context:()=>new Promise(r=>listener({type:'CONVERSATION_CONTEXT'},{},r))};
}
(async()=>{
 let f=fixture();assert.equal(f.requests.length,0);checks++;
 assert.equal((await f.context()).context_key,KEY);assert.equal(f.requests.length,0);checks++;
 const r=await f.run('read');assert.equal(r.ok,true);assert.equal(r.messages[0].text,'history');checks++;
 assert(!JSON.stringify(r).includes('must-not-cross'));assert(!JSON.stringify(r).includes('fixture-token'));checks++;
 assert(f.requests.every(x=>x.method==='GET'));checks++;
 let sent=await f.run('send','once');assert.equal(sent.ok,true);assert.equal(sent.text,'hello');assert.equal(f.appended,1);checks++;
 await f.run('send','once');assert.equal(f.appended,1);checks++;
 f.location.href=OTHER;assert.equal((await f.run('send')).ok,false);assert.equal(f.appended,1);checks++;
 f=fixture();assert.equal((await f.run('send','expired',KEY,0)).ok,false);assert.equal(f.requests.length,0);checks++;
 f=fixture('busy');assert.equal((await f.run('send')).ok,false);assert.equal(f.appended,0);checks++;
 f=fixture('unknown');const bad=await f.run('send');assert.equal(bad.uncertain,true);assert.equal(f.appended,1);checks++;
 await f.run('send');assert.equal(f.appended,1);checks++;
 assert(!f.requests.some(x=>/create-evaluation|model-catalog/.test(x.url)));checks++;
 assert(Core.contextFromUrl(KEY));assert.equal(Core.contextFromUrl('https://example.com/agent/'+ID),null);checks++;
 f=fixture();assert.equal((await f.run('send','large',KEY,Date.now()+30000,'x'.repeat(115000))).ok,true);checks++;
 f=fixture();assert.equal((await f.run('send','oversize',KEY,Date.now()+30000,'x'.repeat(200001))).ok,false);assert.equal(f.requests.length,0);checks++;
 for(const [mode,stage,attempted] of [['verification',3,true],['auth-verification',2,false]]){
  f=fixture(mode);const err=await f.run('send');
  assert.equal(err.error_code,'VERIFICATION_REQUIRED');assert.equal(err.upstream_status,403);assert.equal(err.failure_stage,stage);
  assert.equal(err.submission_attempted,attempted);assert.equal(err.submission_accepted,false);assert.equal(err.uncertain,false);
  assert(!JSON.stringify(err).includes('fixture-token'));assert(!JSON.stringify(err).includes('recaptcha rejected'));checks++;
 }
 for(const [reason,code] of [['参数验证失败','MODEL_ERROR'],['human verification required','VERIFICATION_REQUIRED']]){
  const filter=new Core.TurnFilter({seq:4,messageId:'fixture',lastEventId:'0',onEvent:()=>{}});
  filter.record({seq_num:1,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'error',errorText:reason}})});
  assert.equal(filter.error.code,code);checks++;
 }
 console.log(checks+' conversation content checks PASS; mocked fetch only; zero account requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
