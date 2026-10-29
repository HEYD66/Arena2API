/* Same read-only history endpoint used by the fusion probe's conversation manager.
 * No automatic reads, chat creation, message sends, deletion, cookies or tokens.
 */
(function(root){
 'use strict';
 const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 const KEY=/^https:\/\/arena\.ai\/(agent|work)\/([0-9a-f-]{36})$/i;
 const clean=x=>typeof x==='string'?x.replace(/[\u0000-\u001f\u007f]/g,' ').trim().slice(0,200):'';
 function parse(json){
  const list=Array.isArray(json)?json:['entries','history','items','sessions','chats','data','results'].map(k=>json?.[k]).find(Array.isArray);
  if(!list)throw Error('HISTORY_FORMAT');
  const seen=new Set(),rows=[];
  for(const raw of list){
   const e=raw?.session&&typeof raw.session==='object'?{...raw.session,...raw}:raw;
   const id=e?.id||e?.sessionId||e?.chatId;
   if(typeof id!=='string'||!UUID.test(id)||seen.has(id))continue;
   seen.add(id);
   let mode=(e.mode||e.type||e.kind)==='work'?'work':'agent';
   for(const v of [e.url,e.path]){if(typeof v==='string'){try{const u=new URL(v,'https://arena.ai');const m=u.href.match(KEY);if(m&&m[2].toLowerCase()===id.toLowerCase())mode=m[1];}catch{}}}
   rows.push({context_key:'https://arena.ai/'+mode+'/'+id.toLowerCase(),title:clean(e.title||e.name||e.summary)||'无标题',
    archived:e.archived===true||e.archivedAt!=null,updated:clean(String(e.updatedAt||e.updated_at||e.lastMessageAt||e.createdAt||'' )).slice(0,40)});
  }
  const cursor=json?.pagination?.hasMore===false?null:[json?.nextCursor,json?.cursor,json?.next,json?.pagination?.nextCursor,json?.pagination?.cursor].find(v=>typeof v==='string'&&v)||null;
  if(cursor&&cursor.length>4096)throw Error('HISTORY_FORMAT');
  return {rows,cursor};
 }
 async function get(path){
  if(location.origin!=='https://arena.ai')throw Error('SOURCE_CHANGED');
  const r=await fetch(path,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
  if(r.status===401||r.status===403)throw Error('LOGIN_REQUIRED');
  if(!r.ok)throw Error('HISTORY_FORMAT');
  const text=await r.text();if(text.length>12000000)throw Error('HISTORY_FORMAT');
  return JSON.parse(text);
 }
 async function page(cursor){
  if(cursor!==null&&cursor!==undefined&&(typeof cursor!=='string'||cursor.length>4096))throw Error('HISTORY_FORMAT');
  return parse(await get('/api/history/unified?limit=50&includeArchived=true'+(cursor?'&cursor='+encodeURIComponent(cursor):'')));
 }
 async function verify(target,cursor=null){
  const m=typeof target==='string'&&target.match(KEY);if(!m||!UUID.test(m[2]))throw Error('NOT_IN_HISTORY');
  // Re-read the page where this row was actually discovered in the current account.
  // A recently updated conversation may have moved to the first page.
  let p=await page(cursor);
  if(!p.rows.some(r=>r.context_key===target)&&cursor)p=await page(null);
  if(!p.rows.some(r=>r.context_key===target))throw Error('NOT_IN_HISTORY');
  const j=await get('/api/chat/'+m[2]+'?includeStreamToken=false&recoverRun=false');
  const ctx=root.ArenaFusionChat.contextFromUrl(target);
  try{root.ArenaFusionChat.normalizeSnapshot(j,ctx,'/api');}catch{throw Error('UNSUPPORTED_CHAT');}
  return {context_key:target,title:clean(j.title)||'无标题'};
 }
 const api={parse,page,verify};root.ArenaConversationHistory=api;
 if(typeof module==='object'&&module.exports)module.exports=api;
 if(typeof chrome!=='undefined')chrome.runtime.onMessage.addListener((msg,sender,respond)=>{
  if(msg.type!=='CONVERSATION_CATALOG')return;
  const work=msg.kind==='list'?page(msg.cursor):msg.kind==='verify'?verify(msg.target,msg.cursor):Promise.reject(Error('HISTORY_FORMAT'));
  work.then(data=>respond({ok:true,...data}),e=>respond({ok:false,catalog_error:['LOGIN_REQUIRED','HISTORY_FORMAT','NOT_IN_HISTORY','UNSUPPORTED_CHAT','SOURCE_CHANGED'].includes(e.message)?e.message:'HISTORY_FORMAT'}));return true;
 });
})(typeof globalThis!=='undefined'?globalThis:this);
