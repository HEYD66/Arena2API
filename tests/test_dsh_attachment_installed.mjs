if(!process.env.DSH_NODE_MODULES) throw new Error('Set DSH_NODE_MODULES to the installed DSH resources/app/node_modules directory.');
// Read-only installed DSH adapter test; synthetic attachment service and fetch, no real files/accounts.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const root=(process.env.DSH_NODE_MODULES||'').replace(/[\/]?$/, '/');
let received=null;const original=globalThis.fetch;
const mockFetch=async(url,init)=>{
 assert.equal(String(url),'http://127.0.0.1:9090/v1/chat/completions');received=JSON.parse(init.body);
 const chunk={id:'fixture',object:'chat.completion.chunk',created:0,model:'fixture',choices:[{index:0,delta:{role:'assistant',content:'ok'},finish_reason:null}]};
 return new Response('data: '+JSON.stringify(chunk)+'\n\ndata: '+JSON.stringify({...chunk,choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
};
globalThis.fetch=mockFetch;
try{
 const {PiAiAdapter}=await import(pathToFileURL(root+'@deepseek-ai/dsh-llm-pi-ai/lib/index.js'));
 const api=await import(pathToFileURL(root+'@earendil-works/pi-ai/dist/api/openai-completions.js'));
 const data=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1cAAAAASUVORK5CYII=','base64');
 const ref={attachmentId:'fixture-image',mediaType:'image/png',mimeType:'image/png',width:1,height:1,bytes:data.length,name:'fixture.png'};
 const attachments={readImageRequest:async()=>({data,mediaType:'image/png',width:1,height:1,bytes:data.length})};
 const model={id:'fixture',provider:'fixture',name:'fixture',api:'openai-completions',baseUrl:'http://127.0.0.1:9090/v1',input:['text','image'],reasoning:false,contextWindow:128000,maxTokens:8192,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
 const profile={streamIdleTimeoutMs:300000,modelErrors:new Map(),configuredMaxTokens:new Map(),piProvider:{},headers:{}};
 const snapshot={profiles:new Map([['fixture',profile]]),models:{getModel:()=>model,streamSimple:(m,c,o)=>api.streamSimple(m,c,{...o,fetch:mockFetch})}};
 const adapter=new PiAiAdapter({resolveApiKey:async()=>'fixture-only',resolveAttachments:()=>attachments,resolveImageAccess:()=>undefined});
 for await(const chunk of adapter.streamWithSnapshot({provider:'fixture',model:'fixture',messages:[{role:'user',content:[{type:'text',text:'describe image'},{type:'image',attachment:ref}]}]},snapshot)){}
 assert(received,'installed adapter must make the mocked request');
 const parts=received.messages.flatMap(m=>Array.isArray(m.content)?m.content:[]);
 assert(parts.some(p=>p.type==='image_url'&&p.image_url.url==='data:image/png;base64,'+data.toString('base64')));
 console.log('PASS installed DSH durable-attachment adapter -> OpenAI inline image request; synthetic bytes only, no real network');
}finally{globalThis.fetch=original}
