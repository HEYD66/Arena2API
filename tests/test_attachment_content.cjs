'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),crypto=require('node:crypto');
const {File}=require('node:buffer');
const Core=require('../extension/chrome/conversation/chat-core.js');
const {validateRequest}=require('../desktop/local_cdp_bridge.cjs');
const KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111',ID=KEY.split('/').at(-1);
async function trial(mode='ok'){
 let listener,appended=0,puts=0,payload;const bytes=Buffer.alloc(300000,65),encoded=bytes.toString('base64');
 const location={href:KEY};
 const fetch=async(url,init={})=>{
  const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
  if(url.includes('/api/chat/'+ID+'?'))return json({id:ID,taskId:'chat-agent',lastEventId:'10',title:'fixture',isStreaming:false,messages:[]});
  if(url.endsWith('/storage/generate-agent-upload-url')){const b=JSON.parse(init.body);return json({key:'cas/users/'+ID+'/'+b.hash,uploadUrl:'https://arena.ai/upload-fixture'})}
  if(url.endsWith('/upload-fixture')){puts++;assert.deepEqual(Buffer.from(await init.body.arrayBuffer()),bytes);return new Response('',{status:mode==='upload-fail'?500:200})}
  if(url.endsWith('/chat/trigger-session'))return json({publicAccessToken:'fixture-token'});
  if(url.endsWith('/in/append')){appended++;payload=JSON.parse(init.body);return json({ok:true,seq:4})}
  if(url.endsWith('/out'))return new Response('event: batch\ndata: '+JSON.stringify({records:[{seq_num:11,headers:[['session-in-event-id','4']],body:JSON.stringify({data:{type:'text-delta',delta:'done'}})},{seq_num:12,headers:[['trigger-control','turn-complete'],['session-in-event-id','4']],body:''}]})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
  throw Error('unexpected fixture endpoint');
 };
 const sandbox={ArenaFusionChat:{...Core,createClient:o=>Core.createClient({...o,fetch,crypto:crypto.webcrypto})},location,document:{title:'fixture'},crypto:crypto.webcrypto,File,Uint8Array,atob,Date,Map,Error,Array,setInterval:()=>0,addEventListener:()=>{},chrome:{runtime:{onMessage:{addListener:f=>listener=f},sendMessage:(msg,cb)=>{
  assert.equal(msg.type,'CONVERSATION_ATTACHMENT');if(mode==='source-change'){cb({ok:false});return}
  if(mode==='navigate')location.href='https://arena.ai/agent/22222222-2222-4222-8222-222222222222';
  const data=encoded.slice(msg.offset,msg.offset+262144);cb({ok:true,data,offset:msg.offset,next:msg.offset+data.length,done:msg.offset+data.length===encoded.length});
 }}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/content.js'),'utf8'),sandbox);
 const cmd={kind:'send',request_id:'a'.repeat(32),context_key:KEY,expires_at:Date.now()+30000,text:'read this',attachments:[{name:'sample.png',type:'image/png',size:bytes.length,sha256:mode==='bad-hash'?'0'.repeat(64):crypto.createHash('sha256').update(bytes).digest('hex')}]};
 const run=()=>new Promise(resolve=>listener({type:'CONVERSATION_ACTION',command:cmd},{},resolve));
 const result=await run();await new Promise(setImmediate);
 if(mode==='ok'){
  assert.equal(result.ok,true);assert.equal(puts,1);assert.equal(appended,1);
  assert.equal(payload.payload.message.parts[0].type,'file');assert.equal(payload.payload.message.metadata.uploads[0].filename,'sample.png');
  await run();assert.equal(appended,1);assert.equal(puts,1);
 }else{assert.equal(result.ok,false);assert.equal(appended,0);if(mode!=='upload-fail')assert.equal(puts,0)}
}
(async()=>{
 for(const mode of ['ok','bad-hash','source-change','navigate','upload-fail'])await trial(mode);
 const route='/v1/conversation/attachment?client_id='+'a'.repeat(32)+'&request_id='+'b'.repeat(32)+'&token='+'c'.repeat(64)+'&file_index=0&offset=0';
 assert.equal(validateRequest({id:'fixture',method:'GET',url:'http://127.0.0.1:9090'+route},9090),route);
 assert.throws(()=>validateRequest({id:'fixture',method:'GET',url:'http://127.0.0.1:9090'+route+'&path=/secret'},9090));
 console.log('7 attachment content/transport scenarios PASS; real adapter/core, mocked bytes and HTTP only');
})().catch(e=>{console.error(e);process.exitCode=1});
