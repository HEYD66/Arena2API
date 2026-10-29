const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {validateRequest,normalizeName,selectApplicationShell}=require('../desktop/local_cdp_bridge.cjs');
let checks=0;
const shell={type:'page',url:'file:///fixture/main.html',title:'FACET'};
const unrelated={type:'page',url:'file:///fixture/preview.html',title:'Local preview'};
assert.equal(selectApplicationShell([shell]),shell);checks++;
assert.equal(selectApplicationShell([unrelated,shell]),shell);checks++;
assert.equal(selectApplicationShell([shell,unrelated,{type:'page',url:'https://example.invalid',title:'FACET'}]),shell);checks++;
assert.throws(()=>selectApplicationShell([shell,{...shell,url:'file:///fixture/second.html'}]));checks++;
assert.throws(()=>selectApplicationShell([unrelated]));checks++;
assert.throws(()=>selectApplicationShell([]));checks++;
assert.throws(()=>selectApplicationShell([{...shell,type:'service_worker'}]));checks++;

const req={id:'test',url:'http://127.0.0.1:9090/health',method:'GET'};
assert.equal(validateRequest(req,9090),'/health');checks++;
for(const url of ['http://example.com:9090/health','http://127.0.0.1:9091/health','http://user:pass@127.0.0.1:9090/health','http://127.0.0.1:9090/v1/chat/completions','http://127.0.0.1:9090/v1/conversation/bind','http://127.0.0.1:9090/health?x=1']){assert.throws(()=>validateRequest({...req,url},9090));checks++;}
assert.throws(()=>validateRequest({...req,method:'DELETE'},9090));checks++;
assert.equal(validateRequest({...req,url:'http://localhost:9090/v1/conversation/command?client_id='+'a'.repeat(32)},9090),'/v1/conversation/command?client_id='+'a'.repeat(32));checks++;
assert.equal(normalizeName('实例 5'),'实例5');checks++;
let originals=0,calls=[];
const sandbox={URL,Response,DOMException,Date,Map,Promise,Error,Number,String,setTimeout,clearTimeout,
 fetch:async()=>{originals++;return new Response('original');},__arena2apiLocalRequest:raw=>calls.push(JSON.parse(raw))};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/local-transport.js'),'utf8'),sandbox);
(async()=>{
 assert.equal(await (await sandbox.fetch(req.url)).text(),'original');assert.equal(calls.length,0);checks++;
 sandbox.Arena2APILocalTransport.renew(9090);
 const reply=sandbox.fetch(req.url);assert.equal(calls.length,1);checks++;
 sandbox.Arena2APILocalTransport.reply(calls[0].id,{status:200,body:'{"status":"ok"}'});
 assert.equal((await (await reply).json()).status,'ok');checks++;
 const before=calls.length;await sandbox.fetch('https://arena.ai/api/chat/fixture');assert.equal(calls.length,before);checks++;
 await sandbox.fetch('http://127.0.0.1:9090/v1/chat/completions',{method:'POST',body:'{}'});assert.equal(calls.length,before);checks++;
 const controller=new AbortController(),pending=sandbox.fetch(req.url,{signal:controller.signal});controller.abort();await assert.rejects(pending);assert.equal(calls.at(-1).cancel,true);checks++;
 sandbox.Arena2APILocalTransport.disable();await sandbox.fetch(req.url);assert(originals>=4);checks++;
 console.log(checks+' local transport checks PASS; no account requests or proxy configuration changes');
})().catch(e=>{console.error(e);process.exitCode=1;});
