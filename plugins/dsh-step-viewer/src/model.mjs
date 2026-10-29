/* Read-only projection of DSH's already public, selected-session event source. */
export const LIMIT = 200000;
const stepKey = d => `${d.turn ?? '?'}:${d.step ?? '?'}`;
const callKey = (d,id) => `${stepKey(d)}:${String(id)}`;
const known = new Set(['tool/call','tool/result','assistant/message','assistant/live-chunk','turn/end','step/end','turn/start']);
export function project(snapshot) {
  const all = Array.isArray(snapshot?.entries) ? snapshot.entries : [];
  const entries = all.slice(-20000), settled = new Set(), calls = new Map(), rows = [], live = new Map(), seen = new Set(), ended=new Set();
  for (const {event:e} of entries) if (e?.type==='assistant/message') settled.add(stepKey(e.data||{}));
  const row=(e,kind,title)=>({key:`${e.type}:${e.seq}`,seq:e.seq,turn:e.data?.turn,step:e.data?.step,time:e.time,kind,title,state:'',sections:[]});
  for (const entry of entries) {
    const e=entry?.event;
    if (!e || !known.has(e.type) || typeof e.seq!=='number' || seen.has(e.seq)) continue;
    seen.add(e.seq);const d=e.data||{};
    if(e.type==='turn/end'){ended.add(d.turn);const r=row(e,'end','本轮结束');r.state=String(d.reason?.kind||'结束原因未提供');r.sections=[{label:'结束记录',value:d.reason}];rows.push(r);continue;}
    if(e.type==='tool/call'){
      const key=callKey(d,d.callId);if(calls.has(key)&&calls.get(key).hasCall)continue;
      let r=calls.get(key);if(!r){r=row(e,'tool',String(d.name||'工具'));rows.push(r);calls.set(key,r);}
      r.hasCall=true;r.title=String(d.name||'工具');r.callId=d.callId;r.arguments=d.arguments;
      r.state=r.hasResult?r.state:'已发起 · 等待结果';r.sections.unshift({label:'调用参数',value:d.arguments,code:true});continue;
    }
    if(e.type==='tool/result'){
      const m=d.message||{},id=m.toolCallId??m.source?.callId;if(id===undefined)continue;
      const key=callKey(d,id);let r=calls.get(key);
      if(!r){r=row(e,'tool','工具返回（调用记录不在当前窗口）');rows.push(r);calls.set(key,r);}
      if(r.hasResult)continue;
      r.hasResult=true;r.failed=m.isError===true||d.error!==undefined;r.state=r.failed?'已返回 · 错误':'已返回';
      r.sections.push({label:'工具返回',value:m.content});if(d.error!==undefined)r.sections.push({label:'错误详情',value:d.error});
      continue;
    }
    if(e.type==='assistant/message'){
      const r=row(e,'message','模型返回的内容');r.state=d.interrupted?'已中断':'已接收';r.blocks=d.message?.content||[];rows.push(r);continue;
    }
    if(e.type==='assistant/live-chunk'&&!settled.has(stepKey(d))){
      const key=`${stepKey(d)}:${d.attemptId}`;let r=live.get(key);
      if(!r){r=row(e,'message','模型流式返回');r.state='接收中';r.blocks=[];rows.push(r);live.set(key,r);}
      const c=d.chunk||{},i=c.index;
      if(Number.isInteger(i)&&i>=0&&i<512){
        if(c.type==='block-start')r.blocks[i]={type:c.blockType,text:'',arguments:''};
        if(c.type==='text-delta'||c.type==='reasoning-delta'){
          const b=r.blocks[i]||(r.blocks[i]={type:c.type==='text-delta'?'text':'reasoning',text:''});
          const next=(b.text||'')+(c.text||'');b.text=next.slice(0,LIMIT);if(next.length>LIMIT)b.truncated=true;
        }
        if(c.type==='tool-call-delta'){
          const b=r.blocks[i]||(r.blocks[i]={type:'tool-call',arguments:''});if(c.id!==undefined)b.id=c.id;if(c.name!==undefined)b.name=c.name;
          const next=(b.arguments||'')+(c.argumentsDelta||'');b.arguments=next.slice(0,LIMIT);if(next.length>LIMIT)b.truncated=true;
        }
        if(c.type==='block-end'&&c.block)r.blocks[i]={...c.block};
      }
      if(c.type==='finish')r.state='模型流结束 · '+String(c.reason?.kind||'未提供原因');
    }
  }
  for(const r of rows){
    if(r.kind==='tool'&&!r.hasResult&&ended.has(r.turn))r.state='本轮已结束 · 未收到该工具返回';
    if(r.kind!=='message')continue;
    for(const b of r.blocks){
      if(!b)continue;
      if(b.type==='text'||b.type==='reasoning'){
        if(!b.text)continue;
        r.sections.push({label:b.type==='text'?'模型说明':b.text.includes('【Arena网页进度')?'网页进度摘要（不是工具详情）':'接口已返回的过程文本',value:b.text,truncated:b.truncated});
      }else if(b.type==='tool-call'&&!calls.has(callKey(r,b.id))){
        r.sections.push({label:`模型提出 ${b.name||'工具'}（尚无执行记录）`,value:b.arguments,code:true,truncated:b.truncated});
      }
    }
    delete r.blocks;
  }
  const visible=rows.filter(r=>r.kind!=='message'||r.sections.length);
  return {rows:visible.slice(-500),omitted:Math.max(0,visible.length-500),windowLimited:all.length>20000,hasMore:!!snapshot?.hasMore};
}
const secret=/^(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie|set-cookie)$/i;
export function redact(value, enabled=true, depth=0, seen=new WeakSet()){
  if(!enabled)return value;
  if(depth>18)return '[内容层级过深：显示已省略]';
  if(typeof value==='string')return value.replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi,'Bearer [已遮盖]').replace(/\bsk-[A-Za-z0-9_-]{16,}/g,'[已遮盖]');
  if(!value||typeof value!=='object')return value;
  if(seen.has(value))return '[循环引用]';seen.add(value);
  const result=Array.isArray(value)?value.map(x=>redact(x,true,depth+1,seen)):Object.fromEntries(Object.entries(value).map(([k,v])=>[k,secret.test(k)?'[已遮盖]':redact(v,true,depth+1,seen)]));
  seen.delete(value);return result;
}
export function display(value,mask=true){
  let v=value;if(typeof v==='string'&&v.length<=2000000){try{v=JSON.parse(v);}catch{}}
  let s;try{const safe=redact(v,mask);s=typeof safe==='string'?safe:JSON.stringify(safe,null,2);}catch{s='[无法序列化该记录]';}
  s=s??'[没有提供内容]';return {text:s.slice(0,LIMIT),truncated:s.length>LIMIT};
}
export function snippets(value){
  let v=value;if(typeof v==='string'){try{v=JSON.parse(v);}catch{return [];}}
  if(!v||typeof v!=='object'||Array.isArray(v))return [];
  return ['command','code','script','content','patch','new_text'].filter(k=>typeof v[k]==='string').map(k=>({label:k,value:v[k]}));
}
export function bindingSource(binding){
 let previous, snapshot;
 return {
  subscribe:fn=>binding.eventSource.subscribe(fn),
  getSnapshot(){const current=binding.eventSource.getSnapshot();if(current!==previous){previous=current;snapshot={entries:current.entries,hasMore:current.hasMore,revision:current.revision,sessionKey:binding.sessionId};}return snapshot;}
 };
}
