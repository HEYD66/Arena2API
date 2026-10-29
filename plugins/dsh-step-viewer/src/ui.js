const h=React.createElement;
const css=`.dsh-sv{position:fixed;inset:5vh 5vw;z-index:2147483000;background:#181c23;color:#edf1f7;border:1px solid #526078;border-radius:14px;box-shadow:0 18px 90px #000a;display:flex;flex-direction:column;font:14px/1.55 system-ui,sans-serif;text-align:left}.dsh-sv *{box-sizing:border-box}.dsh-sv button,.dsh-sv input,.dsh-sv select{font:inherit;color:#edf1f7;background:#293447;border:1px solid #65758d;border-radius:6px;padding:6px 10px}.dsh-sv button{cursor:pointer}.dsh-sv button:hover{background:#245a95}.dsh-sv button:focus-visible,.dsh-sv summary:focus-visible,.dsh-sv input:focus-visible{outline:2px solid #9bc8ff;outline-offset:2px}.dsh-sv-head{padding:16px 20px;border-bottom:1px solid #485469;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.dsh-sv h2{font-size:19px;margin:0}.dsh-sv small{color:#bbc7d9}.dsh-sv-toolbar{padding:12px 20px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}.dsh-sv-body{overflow:auto;flex:1;padding:0 20px 20px;min-height:0}.dsh-sv-card{border:1px solid #475468;border-radius:8px;margin:10px 0;background:#202733}.dsh-sv-card>summary{padding:12px;cursor:pointer;overflow-wrap:anywhere}.dsh-sv-badge{display:inline-block;font-size:12px;border:1px solid #60748f;padding:1px 7px;border-radius:5px;margin-left:8px;color:#ccdfff}.dsh-sv-inner{padding:0 14px 14px}.dsh-sv-section{margin-top:12px}.dsh-sv-section header{display:flex;gap:8px;align-items:center;justify-content:space-between;margin:5px 0}.dsh-sv pre{padding:12px;background:#11161e;border:1px solid #4a586d;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto;font:13px/1.6 Consolas,monospace;margin:6px 0;user-select:text}.dsh-sv-alert{color:#ffd294}.dsh-sv-foot{padding:10px 20px;border-top:1px solid #485469;font-size:12px;color:#c0ccdf}.dsh-sv-empty{padding:40px 12px;color:#c0ccdf}.dsh-sv-error{color:#ffb7b7}`;
function Section({label,value,mask,truncated}){
 const [note,setNote]=React.useState('');const shown=display(value,mask);
 const copy=async()=>{try{if(!navigator.clipboard?.writeText)throw Error();await navigator.clipboard.writeText(shown.text);setNote('已复制当前显示');}catch{setNote('复制失败，请选中文本手动复制');}};
 return h('section',{className:'dsh-sv-section'},h('header',null,h('strong',null,label),h('button',{onClick:copy,type:'button'},'复制当前显示')),h('pre',null,shown.text),(truncated||shown.truncated)&&h('small',{className:'dsh-sv-alert'},`显示已截断（上限${LIMIT}字符），原会话记录未修改。`),note&&h('small',{'role':'status'},note));
}
function Card({row,mask,index}){
 const [expanded,setExpanded]=React.useState(false);
 return h('details',{className:'dsh-sv-card',onToggle:e=>setExpanded(e.currentTarget.open)},h('summary',null,h('strong',null,`${index}. ${row.title}`),h('span',{className:'dsh-sv-badge'},row.kind==='tool'?'DSH 工具记录':row.kind==='end'?'控制记录':'模型返回'),h('span',{className:row.failed?'dsh-sv-error':'',style:{marginLeft:10}},row.state),h('br'),h('small',null,`轮 ${row.turn??'—'} · 步 ${row.step??'—'} · 记录序号 ${row.seq}`)),expanded&&h('div',{className:'dsh-sv-inner'},...row.sections.flatMap((s,i)=>[
  h(Section,{...s,mask,key:`s${i}`}),
  ...(s.code?snippets(s.value).map((x,j)=>h(Section,{...x,label:`代码 / ${x.label}`,mask,key:`c${i}:${j}`})):[]),
  ...(s.label==='工具返回'&&Array.isArray(s.value)?s.value.slice(0,10).filter(x=>x?.type==='text').map((x,j)=>h(Section,{label:`返回正文 ${j+1}`,value:x.text,mask,key:`o${i}:${j}`})):[])
 ])));
}
function Panel(props){
 const source=props.useExecutionTrace(),data=React.useMemo(()=>project(source),[source]);
 const [filter,setFilter]=React.useState('all'),[query,setQuery]=React.useState(''),[mask,setMask]=React.useState(true),[count,setCount]=React.useState(100),[loading,setLoading]=React.useState(false),[error,setError]=React.useState('');
 const ref=React.useRef(null),body=React.useRef(null);
 React.useEffect(()=>{const old=document.activeElement;ref.current?.querySelector('button')?.focus();const key=e=>{if(e.key==='Escape'){e.stopPropagation();props.close();}if(e.key==='Tab'){const a=[...ref.current.querySelectorAll('button:not(:disabled),input,select,summary')];if(!a.length)return;if(e.shiftKey&&document.activeElement===a[0]){e.preventDefault();a.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===a.at(-1)){e.preventDefault();a[0].focus();}}};const el=ref.current;el.addEventListener('keydown',key);return()=>{el.removeEventListener('keydown',key);if(old?.isConnected)old.focus();};},[]);
 const rows=data.rows.filter(r=>(filter==='all'||filter==='tools'&&r.kind==='tool'||filter==='model'&&r.kind==='message'||filter==='issues'&&(r.failed||/未收到|中断|error|aborted/.test(r.state)))&&(`${r.title} ${r.turn} ${r.step} ${r.state}`).toLowerCase().includes(query.toLowerCase()));
 const shown=rows.slice(-count);
 const older=async()=>{setLoading(true);setError('');try{await props.loadExecutionOlder();}catch{setError('更早记录读取失败，未执行或重发任何任务。');}finally{setLoading(false);}};
 return ReactDOM.createPortal(h('section',{className:'dsh-sv',role:'dialog','aria-modal':true,'aria-label':'执行过程',ref},h('style',null,css),h('header',{className:'dsh-sv-head'},h('div',{style:{flex:1}},h('h2',null,'执行过程'),h('small',null,'dsh-step-viewer · 只读查看当前会话，不执行、不重放')),h('button',{onClick:props.close,type:'button','aria-label':'关闭执行过程'},'关闭 ×')),
 h('div',{className:'dsh-sv-toolbar'},h('select',{value:filter,onChange:e=>setFilter(e.target.value),'aria-label':'记录类型'},h('option',{value:'all'},'全部过程'),h('option',{value:'tools'},'工具调用 / 返回'),h('option',{value:'model'},'模型说明 / 公开过程'),h('option',{value:'issues'},'错误 / 未完成')),h('input',{placeholder:'工具名称 / 步骤 / 状态',value:query,onChange:e=>setQuery(e.target.value),'aria-label':'筛选执行记录'}),h('label',null,h('input',{type:'checkbox',checked:mask,onChange:e=>setMask(e.target.checked)}),' 遮盖常见密钥字段'),h('button',{onClick:()=>body.current?.scrollTo({top:body.current.scrollHeight,behavior:'smooth'})},'到最新'),h('small',null,`${rows.length} 条记录`)),
 h('div',{className:'dsh-sv-body',ref:body},h('p',null,'展开每条记录，查看实际参数、代码和返回。工具“已返回”不等于文件或任务一定成功；执行位置取决于工具实现。'),h('p',{className:'dsh-sv-alert'},'网页进度摘要会明确标记。未传入DSH的网页原始步骤无法在这里还原。'),
 (data.hasMore||data.windowLimited||data.omitted>0)&&h('p',{className:'dsh-sv-alert'},`当前仅显示已加载窗口内最近记录${data.omitted?`，另有${data.omitted}条超出500条显示上限`:''}。`),
 data.hasMore&&h('button',{onClick:older,disabled:loading},loading?'正在读取历史…':'读取更早的会话记录（只读）'),rows.length>count&&h('button',{onClick:()=>setCount(x=>x+100)},'显示前100条'),
 error&&h('p',{role:'alert',className:'dsh-sv-error'},error),shown.length?shown.map((r,i)=>h(Card,{row:r,mask,index:rows.length-shown.length+i+1,key:r.key})):h('div',{className:'dsh-sv-empty'},'当前已加载的会话没有匹配记录。无需发送测试任务；已有会话记录也可查看。')),
 h('footer',{className:'dsh-sv-foot'},'数据来自DSH当前会话事件。不会读取其他会话文件，不联网、不新增磁盘日志。遮盖无法识别所有代码中的密钥，复制前请检查。关闭插件将卸载入口和订阅。')),document.body);
}
function Entry(props){
 const [open,setOpen]=React.useState(false);
 React.useEffect(()=>{setOpen(false);},[props.sessionId]);
 return h(React.Fragment,null,h('button',{type:'button',onClick:()=>setOpen(true),title:'按步骤查看实际调用参数、代码与返回',style:{background:'#245a95',border:'1px solid #84a5cc',borderRadius:6,color:'#fff',padding:'5px 10px',cursor:'pointer'}},'执行过程'),open&&props.useExecutionTrace&&h(Panel,{...props,close:()=>setOpen(false),key:props.sessionId}));
}
function apply(ctx){
 ctx.uiSession.provide({hooks:['executionTrace'],props:['loadExecutionOlder'],resolve:binding=>({hooks:{executionTrace:bindingSource(binding)},props:{loadExecutionOlder:()=>binding.session.loadOlder()}})});
 ctx.slots.inject('conversation.session.header.utilities',()=>ctx.slots.register({name:'conversation.session.header.utilities',id:'dsh-step-viewer',order:85},Entry));
}
