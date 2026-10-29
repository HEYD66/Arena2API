/* Read-only Arena account-state adapter. No cookies, tokens or credentials are read or exported.
 * Executed in MAIN by chrome.scripting, solely on an Arena tab in this profile.
 * Primary: same-origin GET /api/me -> { user: { id, username, ... } } (works before the sidebar renders).
 * Fallback: SidebarUser -> useUserStore(state => state.user) when /api/me is unreachable or has an unknown shape.
 * Both paths hash the same user id, so existing bindings keep the same account_key.
 */
async function arenaAccountIdentity(){
 if(location.origin!=='https://arena.ai')return {ok:false,code:'WRONG_ORIGIN'};
 const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 async function finish(user,adapter){
  const bytes=new TextEncoder().encode('arena-account-v1|'+user.id.toLowerCase());
  const digest=await crypto.subtle.digest('SHA-256',bytes),account_key=[...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');
  let label=String(user.name||'').replace(/[\u0000-\u001f\u007f]/g,' ').trim().slice(0,80);
  if(label.includes('@'))label=label.slice(0,2)+'***';
  return {ok:true,account_key,label:label||'Arena账号',adapter};
 }
 // 1) 账号接口：后台新开的标签页侧边栏还没渲染时也能识别
 try{
  const res=await fetch('/api/me',{credentials:'include',cache:'no-store',redirect:'error',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
  // 401/403 明确表示未登录：不再去猜页面状态
  if(res.status===401||res.status===403)return {ok:false,code:'IDENTITY_UNAVAILABLE'};
  if(res.ok){
   const u=(await res.json())?.user;
   if(u&&typeof u==='object'&&typeof u.id==='string'&&UUID.test(u.id)){
    if(u.isAnonymous===true||u.is_anonymous===true)return {ok:false,code:'IDENTITY_UNAVAILABLE'};
    return finish({id:u.id,name:typeof u.username==='string'?u.username:''},'api-me-v1');
   }
   if(u===null)return {ok:false,code:'IDENTITY_UNAVAILABLE'};
  }
 }catch{}
 // 2) 接口不可用或格式未知时，退回读侧边栏状态
 const roots=[...document.querySelectorAll('[data-sidebar="footer"]')];
 if(roots.length!==1)return {ok:false,code:'IDENTITY_UNAVAILABLE'};
 const users=new Map(),seen=new Set();let examined=0;
 for(const element of [roots[0],...roots[0].querySelectorAll('*')].slice(0,200)){
  const property=Object.keys(element).find(k=>k.startsWith('__reactFiber$'));let fiber=property?element[property]:null;
  for(let n=0;fiber&&n<12;n++,fiber=fiber.return){
   if(seen.has(fiber))break;seen.add(fiber);if(++examined>500)return {ok:false,code:'IDENTITY_UNAVAILABLE'};
   // Read live hook values only. Do not traverse alternate trees, caches or storage.
   let hook=fiber.memoizedState;
   for(let k=0;hook&&k<40;k++,hook=hook.next){
    for(const value of [hook.memoizedState,hook.queue?.value]){
     if(!value||typeof value!=='object'||typeof value.id!=='string'||!UUID.test(value.id))continue;
     if(typeof value.username!=='string'||typeof value.email!=='string'||!('avatarUrl' in value))continue;
     if(value.isAnonymous===true||value.is_anonymous===true)continue;
     users.set(value.id,{id:value.id,name:value.username});
    }
   }
  }
 }
 if(users.size!==1)return {ok:false,code:users.size?'IDENTITY_AMBIGUOUS':'IDENTITY_UNAVAILABLE'};
 return finish([...users.values()][0],'sidebar-user-v1');
}
if(typeof module==='object'&&module.exports)module.exports=arenaAccountIdentity;
