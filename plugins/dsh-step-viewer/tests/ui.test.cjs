const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://fixture.invalid'});
global.window=dom.window;global.document=dom.window.document;global.navigator=dom.window.navigator;global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),ReactDOM=require('react-dom'),{createRoot}=require('react-dom/client'),{act}=React;
let mod,descriptor,Entry;let callbacks=new Set(),loads=0,clipboard='';let snapshot={entries:[],revision:0,hasMore:true};
Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{clipboard=t;}}});
vm.runInNewContext(fs.readFileSync(process.argv[2]||path.join(__dirname,'../client.js'),'utf8'),{window:{__ModuleLoader__:{load:x=>{mod=x.factory(n=>n==='react'?React:n==='react-dom'?ReactDOM:null)}}},document,navigator,console,Set,Map,WeakSet,Number,Object,JSON,Date});
const binding={sessionId:'fixture-a',eventSource:{subscribe:f=>{callbacks.add(f);return()=>callbacks.delete(f)},getSnapshot:()=>snapshot},session:{loadOlder:async()=>{loads++}}};
mod.apply({uiSession:{provide:d=>{descriptor=d;}},slots:{inject:(name,fn)=>{assert.equal(name,'conversation.session.header.utilities');return fn();},register:(opts,C)=>{Entry=C;return()=>{};}}});
const source=descriptor.resolve(binding).hooks.executionTrace;
function useExecutionTrace(){return React.useSyncExternalStore(source.subscribe,source.getSnapshot);}
const root=createRoot(document.getElementById('root'));
const click=async el=>act(async()=>{el.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));});
(async()=>{
 await act(async()=>root.render(React.createElement(Entry,{sessionId:'fixture-a',useExecutionTrace,loadExecutionOlder:()=>binding.session.loadOlder()})));
 assert.equal(callbacks.size,0);await click(document.querySelector('button'));assert.equal(callbacks.size,1);assert(document.querySelector('[role=dialog]'));assert.match(document.body.textContent,/没有匹配记录/);
 snapshot={revision:1,hasMore:true,entries:[{event:{seq:1,type:'tool/call',data:{turn:1,step:2,callId:'c',name:'write_file',arguments:JSON.stringify({path:'fixture.txt',content:'line1\n<script>window.attack=1</script>',api_key:'SECRET'})}}}]};
 await act(async()=>{for(const f of callbacks)f();});assert.match(document.body.textContent,/write_file/);assert.match(document.body.textContent,/等待结果/);
 const details=document.querySelector('.dsh-sv-card');await act(async()=>{details.open=true;details.dispatchEvent(new window.Event('toggle'));});
 assert(document.querySelectorAll('pre').length>=2);assert(!document.querySelector('script'));assert(!document.body.textContent.includes('SECRET'));assert(document.body.textContent.includes('<script>'));assert.equal(window.attack,undefined);
 await click(document.querySelector('.dsh-sv-section button'));assert(clipboard.includes('已遮盖'));assert(!clipboard.includes('SECRET'));
 await click([...document.querySelectorAll('button')].find(b=>b.textContent.includes('读取更早')));assert.equal(loads,1);
 snapshot={...snapshot,revision:2,entries:[...snapshot.entries,{event:{seq:2,type:'tool/result',data:{turn:1,step:2,message:{toolCallId:'c',content:[{type:'text',text:'WRITTEN_FIXTURE'}],isError:false}}}}]};
 await act(async()=>{for(const f of callbacks)f();});assert.match(document.body.textContent,/已返回/);assert.match(document.body.textContent,/WRITTEN_FIXTURE/);assert.equal(document.querySelectorAll('.dsh-sv-card').length,1);
 await click(document.querySelector('[aria-label="关闭执行过程"]'));assert.equal(callbacks.size,0);assert(!document.querySelector('[role=dialog]'));
 await click(document.querySelector('button'));assert.equal(callbacks.size,1);await act(async()=>root.unmount());assert.equal(callbacks.size,0);assert(!document.querySelector('[role=dialog]'));
 console.log('PASS 12 UI checks: open/close, real-event update, expand code, JSON/results, copy masking, HTML inert, history readonly, no duplicate, unsubscribe on close/disable');
})().catch(e=>{console.error(e);process.exitCode=1;});
