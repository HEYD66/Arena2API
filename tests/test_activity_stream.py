"""Offline activity channel: no fake tokens, no early executable tool calls."""
import asyncio,json,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from conversation_stream import LiveReply,live_response
from openai_compat import normalize_request
from conversation_bridge import ConversationBridge
TOOL={'type':'function','function':{'name':'fixture','parameters':{'type':'object'}}}
CLIENT='a'*32;KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
def data(f):return json.loads(f[6:]) if f.startswith('data: ') and '[DONE]' not in f else {}
class ActivityTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY});self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
  self.b.model_alias='opus5.5';self.b.tool_reference_provider=lambda:True
 async def start(self):
  r=await self.b.completion({'model':'opus5.5','stream':True,'tools':[TOOL],'messages':[{'role':'user','content':'fixture'}]});it=r.body_iterator
  await anext(it);cmd=await asyncio.wait_for(self.b.command(CLIENT),1);self.assertTrue(cmd['activity_stream']);return it,cmd
 def progress(self,c,p):return self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':c['request_id'],'progress':p})
 def finish(self,c,**extra):self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':c['request_id'],'result':{'ok':True,'completed':True,'execution_scope_verified':True,**extra}})
 async def test_real_activity_visible_while_tool_json_held(self):
  it,c=await self.start();text='<tool_call>{"name":"fixture","arguments":{"x":1}}</tool_call>'
  self.progress(c,{'seq':1,'delta':text[:25]})
  self.progress(c,{'seq':2,'kind':'activity','delta':'tool_running','tool':'read_file'})
  f=await asyncio.wait_for(anext(it),1);d=data(f)['choices'][0]['delta'];self.assertIn('reasoning_content',d);self.assertNotIn('tool_calls',d);self.assertNotIn('<tool_call>',str(d));self.assertTrue(self.b.busy)
  self.progress(c,{'seq':3,'delta':text[25:]});self.finish(c,text=text)
  tail=[f async for f in it];self.assertEqual(sum('tool_calls' in data(f).get('choices',[{'delta':{}}])[0]['delta'] for f in tail),1);self.assertFalse(self.b.uncertain)
 async def test_activity_never_contaminates_final_text(self):
  it,c=await self.start();self.progress(c,{'seq':1,'kind':'activity','delta':'tool_returned','tool':'read_file'});self.progress(c,{'seq':2,'delta':'hello'});self.finish(c,text='hello')
  frames=[f async for f in it];self.assertEqual(''.join(data(f).get('choices',[{'delta':{}}])[0]['delta'].get('content','') for f in frames),'hello');self.assertFalse(self.b.uncertain)
 async def test_activity_is_not_success_or_tools_on_failure(self):
  it,c=await self.start();self.progress(c,{'seq':1,'kind':'activity','delta':'submitted'})
  self.finish(c,ok=False,completed=False,uncertain=True,error_code='EXECUTION_SCOPE')
  frames=''.join([f async for f in it]);self.assertIn('EXECUTION_SCOPE',frames);self.assertNotIn('"finish_reason": "stop"',frames);self.assertNotIn('"tool_calls": [',frames);self.assertTrue(self.b.uncertain)
 async def test_activity_without_answer_is_not_success(self):
  it,c=await self.start();self.progress(c,{'seq':1,'kind':'activity','delta':'thinking'});self.finish(c,text='')
  frames=''.join([f async for f in it]);self.assertIn('"error"',frames);self.assertNotIn('"finish_reason": "stop"',frames)
 def test_validation_dedupe_sequence_and_bounds(self):
  s=LiveReply(True)
  for p in [{'seq':1,'kind':'activity','delta':'SECRET'},{'seq':1,'kind':'activity','delta':'thinking','tool':'<tool_call>'},{'seq':1,'kind':'other','delta':'x'},{'seq':2,'kind':'activity','delta':'thinking'}]:
   with self.assertRaises(HTTPException):s.push(p)
  # 固定状态（已提交/思考/结束）不再显示；带工具名的网页工具事件才显示
  s.push({'seq':1,'kind':'activity','delta':'thinking'});self.assertEqual(len(s.events),0)
  s.push({'seq':2,'kind':'activity','delta':'tool_running','tool':'read_file'});s.push({'seq':2,'kind':'activity','delta':'tool_running','tool':'read_file'});self.assertEqual(len(s.events),1)
  self.assertIn('网页工具 · read_file',s.events[0][1]);self.assertNotIn('【Arena网页进度',s.events[0][1])
  for i in range(3,301):s.push({'seq':i,'kind':'activity','delta':'tool_running','tool':'read_file'})
  self.assertEqual(len(s.events),256);self.assertEqual(s.parts,[]);self.assertEqual(s.size,0)
  s.push({'seq':301,'delta':'answer'});self.assertEqual(s.parts,['answer']);self.assertEqual(s.events[-1],('text','answer'))
 def test_unrequested_activity_rejected(self):
  with self.assertRaises(HTTPException):LiveReply().push({'seq':1,'kind':'activity','delta':'thinking'})
 async def test_any_route_with_tools_gets_activity(self):
  # 任意模型名、未开工具文档试验，只要带工具就开启进度提示
  self.b.model_alias='other';self.b.tool_reference_provider=lambda:False
  r=await self.b.completion({'model':'other','stream':True,'tools':[TOOL],'messages':[{'role':'user','content':'fixture'}]});it=r.body_iterator;await anext(it);c=await self.b.command(CLIENT);self.assertTrue(c['activity_stream']);self.finish(c,text='done');await anext(it);await it.aclose()
 async def test_no_tools_no_activity(self):
  self.b.model_alias='other';r=await self.b.completion({'model':'other','stream':True,'messages':[{'role':'user','content':'fixture'}]});it=r.body_iterator;await anext(it);c=await self.b.command(CLIENT);self.assertFalse(c['activity_stream']);self.finish(c,text='done');await anext(it);await it.aclose()

class StepTests(ActivityTests):
 # 用户确认的新边界：思考原文和工具调用摘要进 reasoning_content；文件正文、工具返回不转发
 def reasoning_text(self,frames):return ''.join(data(f).get('choices',[{'delta':{}}])[0]['delta'].get('reasoning_content','') for f in frames)
 async def test_reasoning_streams_with_step_header_never_in_content(self):
  it,c=await self.start();self.progress(c,{'seq':1,'kind':'reasoning','delta':'先看看目录'});self.progress(c,{'seq':2,'delta':'好的'});self.finish(c,text='好的')
  frames=[f async for f in it];r=self.reasoning_text(frames)
  self.assertIn('思考过程 · 第 1 步',r);self.assertIn('先看看目录',r)
  self.assertEqual(''.join(data(f).get('choices',[{'delta':{}}])[0]['delta'].get('content','') for f in frames),'好的')
 async def test_tool_call_summary_streams_without_file_body(self):
  write={'type':'function','function':{'name':'write','parameters':{'type':'object'}}}
  r=await self.b.completion({'model':'opus5.5','stream':True,'tools':[write],'messages':[{'role':'user','content':'fixture'}]});it=r.body_iterator
  await anext(it);c=await asyncio.wait_for(self.b.command(CLIENT),1);self.assertTrue(c['activity_stream'])
  text='<tool_call>{"name":"write","arguments":{"path":"src/app.py","content":"SECRET_FILE_BODY"}}</tool_call>'
  cut=text.index('"content"');self.progress(c,{'seq':1,'delta':text[:cut]})
  f=await asyncio.wait_for(anext(it),1);r=self.reasoning_text([f])
  self.assertIn('写文件 · src/app.py',r);self.assertNotIn('SECRET',r);self.assertNotIn('tool_calls',data(f)['choices'][0]['delta'])
  self.progress(c,{'seq':2,'delta':text[cut:]});self.finish(c,text=text)
  tail=[f async for f in it];self.assertNotIn('SECRET',self.reasoning_text(tail))
  self.assertEqual(sum('tool_calls' in data(f).get('choices',[{'delta':{}}])[0]['delta'] for f in tail),1)
  self.assertEqual(self.reasoning_text([f]+tail).count('写文件 · src/app.py'),1)
 def test_reasoning_rejected_when_not_enabled(self):
  with self.assertRaises(HTTPException):LiveReply().push({'seq':1,'kind':'reasoning','delta':'x'})
async def wire_fixture():
 b,p,agent=normalize_request({'model':'opus5.5','tools':[TOOL],'messages':[{'role':'user','content':'fixture'}]})
 class Fake:
  model_alias='opus5.5';uncertain=False
  def __init__(self):self.ready=asyncio.Event();self.end=asyncio.Event()
  async def queued_send(self,prompt,revision,progress=None,execution_policy=None):
   self.state=progress;self.ready.set();await self.end.wait();return {'ok':True,'completed':True,'text':'<tool_call>{"name":"fixture","arguments":{"x":1}}</tool_call>'}
 f=Fake();r=live_response(f,'fixture',1,p,agent,execution_policy={'activity_stream':True});it=r.body_iterator;initial=[await anext(it)];await f.ready.wait()
 f.state.push({'seq':1,'kind':'activity','delta':'tool_running','tool':'fixture'});initial.append(await anext(it));f.end.set();final=[s async for s in it]
 return {'initial':initial,'final':final}
if __name__=='__main__':
 if len(sys.argv)==2 and sys.argv[1]=='--wire':print(json.dumps(asyncio.run(wire_fixture()),ensure_ascii=False))
 else:unittest.main()
