"""Offline true incremental streaming: no Arena/browser/network requests."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from conversation_bridge import ConversationBridge
from conversation_stream import LiveReply, TextGate, live_response
from openai_compat import normalize_request

CLIENT='e'*32
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
TOOL={'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}
def body(**kw):return dict(model='arena-bound',stream=True,messages=[{'role':'user','content':'fixture'}],**kw)
def data(frame):return json.loads(frame[6:]) if frame.startswith('data: ') and '[DONE]' not in frame else None
def content(frames):return ''.join((data(f) or {}).get('choices',[{'delta':{}}])[0]['delta'].get('content','') for f in frames)

class StreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
    async def start(self, **kw):
        r=await self.b.completion(body(**kw));it=r.body_iterator
        role=await anext(it);self.assertEqual(data(role)['choices'][0]['delta'],{'role':'assistant'})
        cmd=await asyncio.wait_for(self.b.command(CLIENT),1)
        self.assertTrue(cmd['stream']);return r,it,cmd
    def progress(self,cmd,seq,delta,**override):
        return self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'progress':{'seq':seq,'delta':delta},**override})
    def finish(self,cmd,text='hello',**extra):
        self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'completed':True,'text':text,'execution_scope_verified':True,**extra}})
    async def test_text_visible_before_native_completion(self):
        r,it,cmd=await self.start();self.progress(cmd,1,'你');frame=await asyncio.wait_for(anext(it),1)
        self.assertEqual(data(frame)['choices'][0]['delta']['content'],'你');self.assertTrue(self.b.busy)
        self.assertFalse(self.b.pending[cmd['request_id']]['future'].done())
        self.progress(cmd,2,'好😀');frame2=await asyncio.wait_for(anext(it),1)
        self.assertEqual(data(frame2)['choices'][0]['delta']['content'],'好😀')
        self.finish(cmd,'你好😀');tail=[f async for f in it]
        self.assertEqual(content([frame,frame2]+tail),'你好😀');self.assertIn('"finish_reason": "stop"',tail[-2]);self.assertEqual(tail[-1],'data: [DONE]\n\n')
        self.assertEqual(r.headers['x-accel-buffering'],'no');self.assertFalse(self.b.busy)
    async def test_duplicate_progress_not_repeated(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'hello');self.progress(cmd,1,'hello');self.finish(cmd,'hello')
        self.assertEqual(content([f async for f in it]),'hello')
    async def test_gap_rejected_and_final_tail_reconciled(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'a')
        with self.assertRaises(HTTPException):self.progress(cmd,3,'c')
        self.finish(cmd,'abc');self.assertEqual(content([f async for f in it]),'abc')
    async def test_old_extension_without_progress_compatible(self):
        _,it,cmd=await self.start();self.finish(cmd,'legacy result')
        self.assertEqual(content([f async for f in it]),'legacy result')
    async def test_partial_tool_json_never_executable(self):
        _,it,cmd=await self.start(tools=[TOOL]);self.progress(cmd,1,'<tool_')
        pending=asyncio.create_task(anext(it));await asyncio.sleep(0.01);self.assertFalse(pending.done())
        text='<tool_call>{"name":"echo","arguments":{"x":"😀"}}</tool_call>'
        self.progress(cmd,2,text[len('<tool_'):]);await asyncio.sleep(0.01)
        # 此时只允许出现步骤行（reasoning_content），不能出现正文或可执行调用
        if pending.done():self.assertEqual(set(data(pending.result())['choices'][0]['delta']),{'reasoning_content'})
        self.finish(cmd,text);frame=await asyncio.wait_for(pending,1)
        # 带工具的请求会先收到网页进度帧（reasoning_content）；工具调用之前不得出现正文或可执行调用
        while 'tool_calls' not in data(frame)['choices'][0]['delta']:
            self.assertEqual(set(data(frame)['choices'][0]['delta']),{'reasoning_content'})
            frame=await asyncio.wait_for(anext(it),1)
        call=data(frame)['choices'][0]['delta']['tool_calls'][0]
        self.assertEqual(call['function']['name'],'echo');self.assertEqual(json.loads(call['function']['arguments']),{'x':'😀'})
        tail=[f async for f in it];self.assertIn('"finish_reason": "tool_calls"',tail[-2])
    async def test_malformed_tool_output_emits_error_not_finish_stop(self):
        _,it,cmd=await self.start(tools=[TOOL]);text='<tool_call>{broken}</tool_call>';self.progress(cmd,1,text);self.finish(cmd,text)
        frames=[f async for f in it];self.assertTrue(any('"error"' in f for f in frames));self.assertFalse(any('"tool_calls": [' in f or '"finish_reason": "stop"' in f for f in frames))
    async def test_quoted_tag_stream_preserves_text_and_finishes(self):
        _,it,cmd=await self.start(tools=[TOOL])
        parts=['正常说明，标签 `','<tool_','call>` 只是例子。下面继续说明。']
        for seq,part in enumerate(parts,1):self.progress(cmd,seq,part)
        text=''.join(parts);self.finish(cmd,text);frames=[f async for f in it]
        self.assertEqual(content(frames),text)
        self.assertFalse(any('"error"' in f or '"tool_calls": [' in f for f in frames))
        self.assertIn('"finish_reason": "stop"',frames[-2])
    async def test_actual_unclosed_call_after_text_still_fails(self):
        _,it,cmd=await self.start(tools=[TOOL])
        text='准备调用。<tool_call>{"name":"echo","arguments":{}}'
        self.progress(cmd,1,text);self.finish(cmd,text);frames=[f async for f in it]
        self.assertTrue(any('"error"' in f for f in frames))
        self.assertFalse(any('"finish_reason": "stop"' in f or '"tool_calls": [' in f for f in frames))

    async def test_required_tool_does_not_stream_invalid_plain_response(self):
        _,it,cmd=await self.start(tools=[TOOL],tool_choice='required');self.progress(cmd,1,'not a call');self.finish(cmd,'not a call')
        frames=[f async for f in it];self.assertEqual(content(frames),'');self.assertTrue(any('"error"' in f for f in frames))
    async def test_progress_failure_after_partial_no_success_finish(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'part');self.assertIn('part',await anext(it))
        self.finish(cmd,'',ok=False,uncertain=True);tail=[f async for f in it]
        self.assertTrue(any('"error"' in f for f in tail));self.assertFalse(any('"finish_reason": "stop"' in f for f in tail));self.assertTrue(self.b.uncertain)
    async def test_mismatched_final_locks_unknown_result(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'prefix');self.finish(cmd,'different')
        frames=[f async for f in it];self.assertTrue(any('"error"' in f for f in frames));self.assertTrue(self.b.uncertain)
    async def test_mismatch_blocks_next_queued_send_before_unlock(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'prefix')
        next_result=await self.b.completion(body());next_it=next_result.body_iterator;await anext(next_it)
        for _ in range(12):await asyncio.sleep(0)
        self.finish(cmd,'different');first=[f async for f in it];second=[f async for f in next_it]
        self.assertTrue(any('"error"' in f for f in first));self.assertTrue(any('"error"' in f for f in second))
        self.assertTrue(self.b.queues[CLIENT].empty());self.assertTrue(self.b.uncertain)

    async def test_no_success_when_completed_false(self):
        _,it,cmd=await self.start();self.finish(cmd,'partial',completed=False)
        frames=[f async for f in it];self.assertTrue(any('"error"' in f for f in frames))
    async def test_cancel_active_iterator_retains_uncertainty(self):
        _,it,cmd=await self.start();await it.aclose()
        self.assertTrue(self.b.uncertain);self.assertFalse(self.b.busy);self.assertFalse(self.b.pending);self.assertFalse(self.b.completion_gate.locked())
    async def test_close_after_native_done_before_sse_end_retains_uncertainty(self):
        _,it,cmd=await self.start();self.progress(cmd,1,'early');self.finish(cmd,'early')
        for _ in range(12):await asyncio.sleep(0)
        self.assertIn('early',await anext(it));await it.aclose()
        self.assertTrue(self.b.uncertain)

    async def test_cancel_queued_iterator_does_not_send(self):
        _,it1,cmd=await self.start();r2=await self.b.completion(body());it2=r2.body_iterator;await anext(it2)
        for _ in range(12):await asyncio.sleep(0)
        self.assertEqual(self.b.waiting,1);await it2.aclose();self.assertEqual(self.b.waiting,0);self.assertFalse(self.b.uncertain)
        self.finish(cmd,'first');[f async for f in it1];self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_no_dispatch_until_stream_consumed(self):
        r=await self.b.completion(body());self.assertFalse(self.b.busy);self.assertTrue(self.b.queues[CLIENT].empty());await r.body_iterator.aclose()
    async def test_preflight_validation_before_response(self):
        self.b.uncertain=True
        with self.assertRaises(HTTPException) as e:await self.b.completion(body())
        self.assertEqual(e.exception.status_code,409);self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_foreign_progress_rejected(self):
        _,it,cmd=await self.start()
        with self.assertRaises(HTTPException):self.progress(cmd,1,'private',client_id='f'*32)
        self.finish(cmd,'ok');self.assertEqual(content([f async for f in it]),'ok')
    async def test_heartbeat_contains_no_fabricated_text(self):
        _,p,agent=normalize_request(body());r=live_response(self.b,'fixture',self.b.binding['revision'],p,agent,heartbeat=0.01)
        it=r.body_iterator;await anext(it);cmd=await self.b.command(CLIENT)
        self.assertEqual(await asyncio.wait_for(anext(it),1),': keep-alive\n\n')
        self.finish(cmd,'ok');[f async for f in it]
    async def test_actual_asgi_body_has_text_before_final_result(self):
        r=await self.b.completion(body());first_text=asyncio.Event();sent=[]
        async def receive():await asyncio.Future()
        async def send(message):
            sent.append(message)
            if message['type']=='http.response.body' and b'early' in message.get('body',b''):first_text.set()
        task=asyncio.create_task(r({'type':'http','asgi':{'spec_version':'2.0'}},receive,send))
        cmd=await asyncio.wait_for(self.b.command(CLIENT),1);self.progress(cmd,1,'early')
        await asyncio.wait_for(first_text.wait(),1);self.assertFalse(task.done());self.assertTrue(self.b.busy)
        self.finish(cmd,'early late');await asyncio.wait_for(task,1)
        wire=b''.join(m.get('body',b'') for m in sent);self.assertIn(b' late',wire);self.assertEqual(wire.count(b'early'),1)
    async def test_asgi24_send_failure_closes_iterator_and_locks(self):
        import inspect
        from starlette.responses import StreamingResponse
        if 'spec_version' not in inspect.getsource(StreamingResponse.__call__):
            self.skipTest('Installed Starlette uses the legacy disconnect path, tested separately')
        r=await self.b.completion(body())
        async def receive():await asyncio.Future()
        async def send(message):
            if message['type']=='http.response.body' and b'early' in message.get('body',b''):
                raise OSError('fixture disconnected')
        task=asyncio.create_task(r({'type':'http','asgi':{'spec_version':'2.4'}},receive,send))
        cmd=await asyncio.wait_for(self.b.command(CLIENT),1);self.progress(cmd,1,'early')
        from starlette.requests import ClientDisconnect
        with self.assertRaises(ClientDisconnect):await asyncio.wait_for(task,1)
        self.assertTrue(self.b.uncertain);self.assertFalse(self.b.busy);self.assertFalse(self.b.completion_gate.locked())

    async def test_asgi_disconnect_cancels_active_send(self):
        r=await self.b.completion(body());disconnect=asyncio.Event()
        async def receive():await disconnect.wait();return {'type':'http.disconnect'}
        async def send(message):pass
        task=asyncio.create_task(r({'type':'http','asgi':{'spec_version':'2.0'}},receive,send))
        await asyncio.wait_for(self.b.command(CLIENT),1);disconnect.set();await asyncio.wait_for(task,1)
        self.assertTrue(self.b.uncertain);self.assertFalse(self.b.busy)

class ProgressTests(unittest.TestCase):
    def test_units_limit_and_sequence_validation(self):
        for p in ({'seq':True,'delta':'x'},{'seq':1,'delta':'😀'*8001},{'seq':0,'delta':'x'},{'seq':1,'delta':''}):
            with self.assertRaises(HTTPException):LiveReply().push(p)
    def test_total_limit_marks_stream_failure(self):
        s=LiveReply()
        for i in range(1,63):s.push({'seq':i,'delta':'x'*16000})
        with self.assertRaises(HTTPException):s.push({'seq':63,'delta':'x'*16000})
        self.assertIsNotNone(s.failure)
    def test_split_tag_never_leaks(self):
        _,p,_=normalize_request(body(tools=[TOOL]));g=TextGate(p)
        out=''.join(g.feed(c) for c in 'hello <tool_call>{"name":"echo"}</tool_call>')
        self.assertEqual(out,'hello ')
    def test_literal_less_than_not_lost(self):
        _,p,_=normalize_request(body(tools=[TOOL]));g=TextGate(p)
        out=''.join(g.feed(c) for c in 'x < y and <t')+g.held
        self.assertEqual(out,'x < y and <t')

if __name__=='__main__':unittest.main(verbosity=2)
