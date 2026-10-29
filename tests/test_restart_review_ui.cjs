'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../desktop/console.html'),'utf8');
// 按函数声明截取到下一个函数声明之前，兼容压缩版和格式化后的多行源码
function slice(start,next){const a=html.search(new RegExp('^\\s*'+start,'m'));if(a<0)throw Error('missing '+start);const b=html.slice(a+1).search(new RegExp('\\n\\s*'+next));return html.slice(a,a+1+b);}
const action=slice('async function routeAction\\(','async function routeTag\\(');
const card=slice('function routeCard\\(','function renderAccounts\\(');
const row={alias:'fixture',title:'fixture',source_label:'fixture',client_id:'a',account_key:'account',online:false,enabled:false,uncertain:true,review_available:true,waiting:0};
function fixture(approve=false){const calls=[],buttons=[],warnings=[],toasts=[];let ack=true;
 const context={state:{sources:[{client_id:'a',account_key:'account'}],menus:new Set()},element:()=>({append(){},classList:{},isConnected:true}),button:(text,fn,disabled=false)=>{const b={text,fn,disabled};buttons.push(b);return b},confirm:text=>{warnings.push(text);return approve},api:async(name,kind,data)=>{calls.push({name,kind,data});return data.action==='check'?{ok:true,review_required:true,resolved:false,review_token:'fixture-only'}:{review_acknowledged:ack}},toast:x=>toasts.push(x),copy(){},navigate(){},gatewayLogs(){},referenceAction(){}};
 context.localButton=context.button;context.toggleControl=(label,on,fn,disabled)=>context.button(label,fn,disabled);context.editModel=()=>{};
 vm.runInNewContext(card+'\n'+action,context);return {context,calls,buttons,warnings,toasts,setAck:v=>ack=v};}
(async()=>{let count=0;
 let f=fixture();f.context.routeCard(row);assert(f.buttons.filter(b=>b.text==='核对上轮').every(b=>!b.disabled));count++;
 f=fixture();f.context.routeCard({...row,review_available:false});assert(f.buttons.filter(b=>b.text==='核对上轮').every(b=>b.disabled));count++;
 f=fixture();f.context.routeCard({...row,busy:true});assert(f.buttons.filter(b=>b.text==='核对上轮').every(b=>b.disabled));count++;
 f=fixture(false);await f.context.routeAction('check',row);assert.equal(f.calls.length,1);assert.equal(f.calls[0].data.action,'check');assert.equal(f.warnings.length,1);assert(f.warnings[0].includes('不代表服务端成功'));count++;
 f=fixture(true);await f.context.routeAction('check',row);assert.equal(f.calls.length,2);assert.equal(f.calls[1].data.action,'acknowledge_review');assert.equal(f.calls[1].data.acknowledged,true);assert.equal(f.calls[1].data.review_token,'fixture-only');assert(f.calls.every(c=>c.kind==='control'));assert(f.toasts[0].includes('绑定保持停用'));count++;
 f=fixture(true);f.setAck(false);await assert.rejects(f.context.routeAction('check',row),/未自动恢复/);assert.equal(f.calls.length,2);count++;
 assert(/r\.uncertain,\s*r\.review_available,\s*r\.waiting/.test(html));count++;
 console.log(count+' restart review UI checks PASS; actual console functions, fixture API only');
})().catch(e=>{console.error(e);process.exitCode=1});
