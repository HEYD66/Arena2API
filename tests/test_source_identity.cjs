'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),crypto=require('node:crypto');
const code=fs.readFileSync(require('node:path').join(__dirname,'../extension/chrome/conversation/identity.js'),'utf8');
const uid='11111111-1111-4111-8111-111111111111';
function fixture(users,origin='https://arena.ai',fetchImpl=()=>{throw Error('NETWORK FORBIDDEN')}){
 const nodes=(users||[]).map(u=>({'__reactFiber$fixture':{memoizedState:{memoizedState:u,next:null},return:null}}));
 const root={querySelectorAll:()=>nodes};let calls=0;
 const context={module:{exports:{}},location:{origin},TextEncoder,crypto:crypto.webcrypto,document:{querySelectorAll:()=>{calls++;return users===null?[]:[root]},get cookie(){throw Error('COOKIE ACCESS FORBIDDEN')}},fetch:fetchImpl,AbortSignal};
 vm.runInNewContext(code,context);return {read:context.module.exports,calls:()=>calls};
}
const user=(extra={})=>({id:uid,username:'fixture',email:'fixture@example.invalid',avatarUrl:null,...extra});
test('stable hash, no raw ID/email/session exported',async()=>{const r=await fixture([user()]).read();assert(r.ok);assert.match(r.account_key,/^[0-9a-f]{64}$/);assert.equal(r.account_key,crypto.createHash('sha256').update('arena-account-v1|'+uid).digest('hex'));assert(!JSON.stringify(r).includes(uid));assert(!JSON.stringify(r).includes('example.invalid'))});
test('display name changes do not change account identity',async()=>{const a=await fixture([user()]).read(),b=await fixture([user({username:'renamed'})]).read();assert.equal(a.account_key,b.account_key);assert.notEqual(a.label,b.label)});
test('two account IDs are ambiguous, never pick first',async()=>{const r=await fixture([user(),user({id:'22222222-2222-4222-8222-222222222222'})]).read();assert.equal(r.ok,false);assert.equal(r.code,'IDENTITY_AMBIGUOUS')});
test('logged-out or unsupported shape fails closed',async()=>{const r=await fixture([]).read();assert.equal(r.ok,false);assert.equal((await fixture([{username:'only-name'}]).read()).ok,false)});
test('foreign origin never inspects DOM',async()=>{const f=fixture([user()],'https://elsewhere.invalid');assert.equal((await f.read()).ok,false);assert.equal(f.calls(),0)});
test('email-like display labels are masked',async()=>{const r=await fixture([user({username:'fixture@example.invalid'})]).read();assert.equal(r.label,'fi***')});
test('other account fields and tokens are not accessed',async()=>{const u=user();Object.defineProperty(u,'access_token',{get(){throw Error('FORBIDDEN')}});assert((await fixture([u]).read()).ok)});

// /api/me 优先：侧边栏未渲染也能识别，且与侧边栏路径得到同一个 account_key
const me=(body,status=200)=>async(url,init)=>{assert.equal(url,'/api/me');assert.equal(init.credentials,'include');return {ok:status>=200&&status<300,status,json:async()=>body}};
test('api/me identifies before sidebar renders, same key as sidebar',async()=>{const a=await fixture(null,'https://arena.ai',me({user:user()})).read(),b=await fixture([user()]).read();assert(a.ok);assert.equal(a.adapter,'api-me-v1');assert.equal(a.account_key,b.account_key);assert(!JSON.stringify(a).includes(uid))});
test('api/me 401 or anonymous fails closed without sidebar guess',async()=>{const f=fixture([user()],'https://arena.ai',me({},401));assert.equal((await f.read()).ok,false);assert.equal(f.calls(),0);assert.equal((await fixture([user()],'https://arena.ai',me({user:user({isAnonymous:true})})).read()).ok,false)});
test('api/me unknown shape or network error falls back to sidebar',async()=>{const r=await fixture([user()],'https://arena.ai',me({profile:{}})).read();assert(r.ok);assert.equal(r.adapter,'sidebar-user-v1');const e=await fixture([user()],'https://arena.ai',async()=>{throw Error('offline')}).read();assert.equal(e.adapter,'sidebar-user-v1')});
test('api/me email label masked',async()=>{const r=await fixture(null,'https://arena.ai',me({user:user({username:'fixture@example.invalid'})})).read();assert.equal(r.label,'fi***')});
