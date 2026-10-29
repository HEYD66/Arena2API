/* Protocol 3: profile-local, opt-in source registration; no browser-instance enumeration. */
(() => {
 const store=keys=>new Promise(r=>chrome.storage.local.get(keys,r));
 const save=value=>new Promise((r,j)=>chrome.storage.local.set(value,()=>chrome.runtime.lastError?j(Error('扩展设置保存失败，状态未确认')):r()));
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 const session=crypto.randomUUID().replaceAll('-','');
 let clientId,config={},sourceTab=null,account=null,offered=false,running=false,ticking=false,lastError='',active=null,configBusy=false;
 const rows=new Map(),targets=new Map();
 const ready=(async()=>{const x=await store(['conversationClientId','workspaceEnabled','workspaceUrl','workspaceLabel']);clientId=/^[0-9a-f]{32}$/.test(x.conversationClientId||'')?x.conversationClientId:crypto.randomUUID().replaceAll('-','');config=x;await save({conversationClientId:clientId});})();
 const lease=()=>globalThis.Arena2APILocalTransport?.source?.();
 const enabled=()=>config.workspaceEnabled===false?false:(config.workspaceEnabled===true||!!lease());
 function validateEndpoint(raw){const u=new URL(raw);if(u.protocol!=='http:'||!['127.0.0.1','localhost'].includes(u.hostname)||u.username||u.password||!Number.isInteger(Number(u.port))||Number(u.port)<1024||Number(u.port)>65535||u.pathname!=='/'||u.search||u.hash)throw Error('仅允许本机HTTP网关根地址');return u.origin;}
 function endpoint(){return validateEndpoint(globalThis.Arena2APILocalTransport?.endpoint?.()||config.workspaceUrl||'http://127.0.0.1:9090')}
 const tabs=()=>new Promise((r,j)=>chrome.tabs.query({url:['https://arena.ai/*']},v=>chrome.runtime.lastError?j(Error('无法读取本配置的Arena标签')):r(v||[])));
 const message=(id,msg)=>new Promise((r,j)=>chrome.tabs.sendMessage(id,msg,v=>chrome.runtime.lastError?j(Error('请刷新此浏览器的Arena页面以加载新版扩展')):v?r(v):j(Error('页面未响应'))));
 const identity=id=>new Promise((r,j)=>chrome.scripting.executeScript({target:{tabId:id},world:'MAIN',func:arenaAccountIdentity},v=>chrome.runtime.lastError?j(Error('账号状态读取失败，请刷新页面')):r(v?.[0]?.result||{ok:false})));
 async function request(path,body,timeout=25000,origin=endpoint()){
  const c=new AbortController(),timer=setTimeout(()=>c.abort(),timeout);
  try{const r=await fetch(origin+path,{method:body===undefined?'GET':'POST',redirect:'error',cache:'no-store',signal:c.signal,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error('网关拒绝请求 HTTP '+r.status);return await r.json();}finally{clearTimeout(timer)}
 }
 async function identify(){
  const all=await tabs();const tab=all.find(t=>t.id===sourceTab)||all.find(t=>t.active)||all[0];
  if(!tab)throw Error('请在此浏览器配置中打开并登录Arena');
  const next=await identity(tab.id);if(!next.ok){account=null;targets.clear();rows.clear();throw Error('无法唯一核验账号身份；未提供发送来源，请展开侧栏并刷新Arena页面')}
  if(account&&account.account_key!==next.account_key){targets.clear();rows.clear();offered=false}
  sourceTab=tab.id;account=next;return next;
 }
 async function offer(){
  if(!enabled())return false;
  let a;try{a=await identify()}catch(e){await request('/v1/conversation/offer',{protocol:3,client_id:clientId,session,account_key:null,identity_status:'unavailable',label:'身份未核验',source_label:config.workspaceLabel||lease()?.name||'浏览器 '+clientId.slice(-6),transport:lease()?'bridge':'direct',history_enabled:config.workspaceEnabled===true});throw e}
  await request('/v1/conversation/offer',{protocol:3,attachment_protocol:1,client_id:clientId,session,account_key:a.account_key,label:a.label,source_label:config.workspaceLabel||lease()?.name||'浏览器 '+clientId.slice(-6),transport:lease()?'bridge':'direct',history_enabled:config.workspaceEnabled===true});
  offered=enabled();lastError='';return offered;
 }
 async function targetTab(target){
  const matching=(await tabs()).filter(t=>{try{const u=new URL(t.url);return u.origin+u.pathname.replace(/\/$/,'')===target}catch{return false}});
  if(matching.length>1)throw Error('同一对话有多个标签，请保留一个后重试');
  const tab=matching[0]||await new Promise((r,j)=>chrome.tabs.create({url:target,active:false},v=>chrome.runtime.lastError?j(Error('打开已有对话失败')):r(v)));
  for(let i=0;i<40;i++){try{const c=await message(tab.id,{type:'CONVERSATION_CONTEXT'});if(c.context_key===target)return tab.id}catch{}await wait(250)}
  throw Error('已有对话页面尚未就绪');
 }
 async function dispatch(cmd,operation){
  const a=await identify();if(a.account_key!==cmd.account_key||session!==cmd.source_session)throw Error('账号或来源已变化，停止操作');
  if(cmd.kind==='catalog_list'){
   const result=await message(sourceTab,{type:'CONVERSATION_CATALOG',kind:'list',cursor:cmd.cursor});
   if(result.ok){if(!cmd.cursor)rows.clear();for(const row of result.rows||[])rows.set(row.context_key,{...row,cursor:cmd.cursor||null});if(rows.size>5000){rows.clear();throw Error('历史元数据缓存已达上限')}}
   return result;
  }
  if(cmd.kind==='catalog_select'){
   const row=rows.get(cmd.target);if(!row)return {ok:false,catalog_error:'NOT_IN_HISTORY'};
   const result=await message(sourceTab,{type:'CONVERSATION_CATALOG',kind:'verify',target:cmd.target,cursor:row.cursor});if(!result.ok)return result;
   let id;try{id=await targetTab(cmd.target)}catch(e){lastError=e.message;return {ok:false,catalog_error:/多个标签/.test(e.message)?'TAB_AMBIGUOUS':'PAGE_NOT_READY'}}
   // 新开的后台标签页侧边栏可能还没渲染出账号信息：短暂重试，仍读不到则如实报告，不当作账号变化
   let check={ok:false};for(let i=0;i<20&&!check.ok;i++){try{check=await identity(id)}catch{}if(!check.ok)await wait(500)}
   if(!check.ok){lastError='目标会话页面未显示账号信息';return {ok:false,catalog_error:'IDENTITY_UNAVAILABLE'}}
   if(check.account_key!==a.account_key)return {ok:false,catalog_error:'SOURCE_CHANGED'};
   targets.set(cmd.target,{tab:id,account_key:a.account_key});return {ok:true,context_key:cmd.target};
  }
  const target=targets.get(cmd.context_key);if(!target||target.account_key!==a.account_key)return {ok:false,uncertain:false,error_code:'CONTEXT_CHANGED'};
  const check=await identity(target.tab);if(!check.ok||check.account_key!==a.account_key){targets.delete(cmd.context_key);return {ok:false,uncertain:false,error_code:'ACCOUNT_CHANGED'}}
  if(cmd.attachments?.length){const cap=await message(target.tab,{type:'CONVERSATION_CONTEXT'});if(cap.context_key!==cmd.context_key||cap.attachment_protocol!==1)return {ok:false,uncertain:false,error_code:'CLIENT_SCOPE_UNVERIFIED'}}
  operation.tab=target.tab;operation.dispatched=true;
  return message(target.tab,{type:'CONVERSATION_ACTION',command:cmd});
 }
 async function loop(){if(running)return;running=true;
  try{while(enabled()){
   if(!offered){try{await offer()}catch(e){lastError=e.message;await wait(3000);continue}}
   try{
    const origin=endpoint();const cmd=await request('/v1/conversation/command?client_id='+clientId,undefined,25000,origin);if(cmd.kind==='idle')continue;
    const op={origin,cmd,tab:null,dispatched:false};active=op;let result;
    try{if(configBusy||!enabled()||origin!==endpoint()||Date.now()>cmd.expires_at)throw Error('来源已关闭或命令过期');result=await dispatch(cmd,op)}
    catch(e){lastError=e.message;result={ok:false,uncertain:op.dispatched&&cmd.kind==='send',catalog_error:'SOURCE_CHANGED',error_code:op.dispatched?'NETWORK_ERROR':'CONTEXT_CHANGED'}}

    for(let i=0;i<3;i++){try{await request('/v1/conversation/result',{protocol:3,client_id:clientId,session,account_key:cmd.account_key,request_id:cmd.request_id,context_key:cmd.context_key,result},25000,origin);break}catch(e){lastError=e.message;if(i<2)await wait(1000)}}
    if(active===op)active=null;
   }catch(e){active=null;offered=false;lastError=e.message;await wait(3000)}
  }}finally{running=false}
 }
 chrome.runtime.onMessage.addListener((msg,sender,respond)=>{
  if(msg.type==='CONVERSATION_ATTACHMENT'){
   const a=active,i=msg.file_index,o=msg.offset;
   if(!enabled()||!a||a.cmd.kind!=='send'||sender.tab?.id!==a.tab||msg.request_id!==a.cmd.request_id||msg.context_key!==a.cmd.context_key||endpoint()!==a.origin||session!==a.cmd.source_session||!Number.isInteger(i)||i<0||i>19||!a.cmd.attachments?.[i]||!Number.isInteger(o)||o<0||o%262144||o>69905068||!/^[0-9a-f]{64}$/.test(a.cmd.attachment_token||'')){respond({ok:false});return}
   const path='/v1/conversation/attachment?client_id='+clientId+'&request_id='+a.cmd.request_id+'&token='+a.cmd.attachment_token+'&file_index='+i+'&offset='+o;
   identity(a.tab).then(check=>{if(!check.ok||check.account_key!==a.cmd.account_key||active!==a)throw Error('Source changed');return request(path,undefined,25000,a.origin)}).then(data=>respond({ok:true,...data}),()=>respond({ok:false}));return true;
  }
  if(msg.type==='CONVERSATION_PROGRESS'){
   const a=active,p=msg.progress;
   if(!enabled()||!a||a.cmd.kind!=='send'||a.cmd.stream!==true||sender.tab?.id!==a.tab||msg.request_id!==a.cmd.request_id||msg.context_key!==a.cmd.context_key||!p||!Number.isInteger(p.seq)||p.seq<1||typeof p.delta!=='string'||!p.delta||p.delta.length>16000){respond({ok:false});return}
   const kind=p.kind||'text';
   if(!['text','activity','reasoning'].includes(kind)||(kind==='reasoning'&&a.cmd.activity_stream!==true)||(kind==='activity'&&(a.cmd.activity_stream!==true||!['submitted','thinking','thought','tool_input','tool_running','tool_returned','tool_error','generating','received'].includes(p.delta)||p.tool!==undefined&&(typeof p.tool!=='string'||!/^[A-Za-z_][A-Za-z0-9_.-]{0,79}$/.test(p.tool))))){respond({ok:false});return}
   const progress={seq:p.seq,delta:p.delta,...(kind==='activity'?{kind, ...(p.tool?{tool:p.tool}:{})}:kind==='reasoning'?{kind}:{})};
   request('/v1/conversation/result',{protocol:3,client_id:clientId,session,account_key:a.cmd.account_key,request_id:a.cmd.request_id,context_key:a.cmd.context_key,progress},10000,a.origin).then(()=>respond({ok:true}),()=>respond({ok:false}));return true;
  }
  if(msg.type==='CONVERSATION_STATUS'){ready.then(()=>respond({enabled:enabled(),offered,account:account?.label||'',error:lastError,source:config.workspaceLabel||'',url:config.workspaceUrl||'http://127.0.0.1:9090',transport:lease()?'bridge':'direct'}));return true}
  if(msg.type==='WORKSPACE_RESET'){
   if(active||configBusy||msg.confirmed!==true){respond({ok:false,error:'请先停止请求并确认重置；不会迁移旧绑定'});return}
   configBusy=true;ready.then(async()=>{try{if(active)throw Error('请求仍在进行，请稍后重置');const nextId=crypto.randomUUID().replaceAll('-','');await save({conversationClientId:nextId,workspaceEnabled:false});config.workspaceEnabled=false;clientId=nextId;offered=false;targets.clear();rows.clear();respond({ok:true});setTimeout(()=>chrome.runtime.reload(),100)}catch(e){respond({ok:false,error:e.message})}finally{configBusy=false}}).catch(()=>{configBusy=false;respond({ok:false,error:'扩展配置尚未就绪，请刷新页面'})});return true;
  }
  if(msg.type==='WORKSPACE_CONFIG'){
   if(active||configBusy){respond({ok:false,error:'请求或设置保存仍在进行，请稍后切换接入'});return}
   configBusy=true;
   ready.then(async()=>{const old=config;try{if(active)throw Error('请求仍在进行，请等待完成后停止接入');const next={...config,workspaceEnabled:msg.enabled===true,workspaceLabel:msg.label===undefined?(config.workspaceLabel||''):String(msg.label||'').trim().slice(0,80),workspaceUrl:String(msg.url||'')};validateEndpoint(next.workspaceUrl);await save(next);config=next;if(old.workspaceUrl!==config.workspaceUrl||config.workspaceEnabled===false){targets.clear();rows.clear()}offered=false;lastError='';respond({ok:true});void tick()}catch(e){config=old;respond({ok:false,error:e.message})}finally{configBusy=false}}).catch(()=>{configBusy=false;respond({ok:false,error:'扩展配置尚未就绪，请刷新页面'})});return true;
  }
  if(msg.type==='CONVERSATION_REFRESH'){tick().then(()=>respond({ok:offered,error:lastError}));return true}
 });
 async function tick(){await ready;if(ticking||!enabled())return;ticking=true;try{await offer();void loop()}catch(e){offered=false;lastError=e.message}finally{ticking=false}}
 setInterval(()=>void tick(),4000);void ready.then(tick);
})();
