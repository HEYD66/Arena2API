"""Offline title isolation, bounded scheduling, cancellation and response tests."""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from conversation_bridge import ConversationBridge, DSH_TITLE_SYSTEM, DSH_TITLE_PREFIX
from fastapi import HTTPException
CLIENT='c'*32
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'

def body(text='hello', **kw):
    return dict(model='arena-bound',messages=[{'role':'user','content':text}],**kw)
def title_body(**kw):
    return dict(model='arena-bound',messages=[{'role':'system','content':DSH_TITLE_SYSTEM},{'role':'user','content':DSH_TITLE_PREFIX+'\n'+json.dumps([{'seq':9,'text':'hi'}])}],**kw)
async def settle():
    for _ in range(12): await asyncio.sleep(0)
class Request:
    def __init__(self):self.gone=False
    async def is_disconnected(self):return self.gone

class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
    async def command(self):return await asyncio.wait_for(self.b.command(CLIENT),1)
    def result(self,cmd,**kw):self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'text':'OK',**kw}})
    async def start(self):
        t=asyncio.create_task(self.b.completion(body('first')));return t,await self.command()
    async def test_title_local_even_while_main_busy(self):
        task,cmd=await self.start()
        out=await self.b.completion(title_body())
        self.assertEqual(out['choices'][0]['message']['content'],'hi')
        self.assertTrue(self.b.busy);self.assertEqual(len(self.b.pending),1);self.assertTrue(self.b.queues[CLIENT].empty())
        self.result(cmd);await task
    async def test_title_sse_and_no_policy_mutation(self):
        b=title_body(stream=True);original=copy.deepcopy(b)
        r=await self.b.completion(b);chunks=[x async for x in r.body_iterator]
        self.assertIn('hi',chunks[0]);self.assertEqual(chunks[-1],'data: [DONE]\n\n');self.assertEqual(b,original)
        self.assertFalse(self.b.pending);self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_similar_but_not_exact_system_is_normal_request(self):
        b=title_body();b['messages'][0]['content']+=' Also explain your reasoning.'
        self.b.execute=AsyncMock(return_value={'ok':True,'text':'normal'})
        await self.b.completion(b);self.b.execute.assert_awaited_once()
    async def test_tools_prevent_title_interception(self):
        b=title_body(tools=[{'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}])
        self.b.execute=AsyncMock(return_value={'ok':True,'text':'normal'})
        await self.b.completion(b);self.b.execute.assert_awaited_once()
    async def test_malformed_title_rows_not_intercepted(self):
        for payload in ('[]','{}','[{"seq":true,"text":"hi"}]','[{"seq":1,"text":"hi","extra":1}]','not json'):
            b=title_body();b['messages'][1]['content']=DSH_TITLE_PREFIX+payload
            self.b.execute=AsyncMock(return_value={'ok':True,'text':'normal'})
            await self.b.completion(b);self.b.execute.assert_awaited_once()
    async def test_local_title_requires_binding_authorization(self):
        self.b.binding['allow_clients']=False
        with self.assertRaises(HTTPException) as e:await self.b.completion(title_body())
        self.assertEqual(e.exception.status_code,403)
    async def test_cjk_local_title_short_plain_text(self):
        b=title_body();b['messages'][1]['content']=DSH_TITLE_PREFIX+json.dumps([{'seq':1,'text':'修复工具调用失败的问题\n不要换行'}])
        r=await self.b.completion(b);text=r['choices'][0]['message']['content']
        self.assertLessEqual(len(text),10);self.assertNotIn('\n',text)
    async def test_fifo_main_requests_no_409(self):
        first,cmd=await self.start();second=asyncio.create_task(self.b.completion(body('second')));third=asyncio.create_task(self.b.completion(body('third')))
        await settle();self.assertEqual(self.b.waiting,2);self.assertTrue(self.b.queues[CLIENT].empty())
        self.result(cmd);await first
        cmd=await self.command();self.assertIn('second',cmd['text']);self.result(cmd);await second
        cmd=await self.command();self.assertIn('third',cmd['text']);self.result(cmd);await third
        self.assertEqual(self.b.waiting,0);self.assertFalse(self.b.completion_gate.locked())
    async def test_waits_for_manual_read(self):
        read=asyncio.create_task(self.b.execute('read',revision=self.b.binding['revision']));cmd=await self.command()
        send=asyncio.create_task(self.b.completion(body()));await settle();self.assertTrue(self.b.queues[CLIENT].empty())
        self.result(cmd);await read;cmd=await self.command();self.assertEqual(cmd['kind'],'send');self.result(cmd);await send
    async def test_cancelled_waiter_never_dispatches(self):
        first,cmd=await self.start();second=asyncio.create_task(self.b.completion(body('cancel me')));await settle();second.cancel()
        with self.assertRaises(asyncio.CancelledError):await second
        self.assertEqual(self.b.waiting,0);self.assertFalse(self.b.uncertain)
        self.result(cmd);await first;self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_queue_full_rejects_without_affecting_active(self):
        self.b.max_waiting=1;first,cmd=await self.start();second=asyncio.create_task(self.b.completion(body('second')));await settle()
        with self.assertRaises(HTTPException) as e:await self.b.completion(body('overflow'))
        self.assertEqual(e.exception.status_code,429)
        self.result(cmd);await first;cmd=await self.command();self.result(cmd);await second
    async def test_queue_timeout_does_not_cancel_active(self):
        self.b.queue_timeout=0.01;first,cmd=await self.start()
        with self.assertRaises(HTTPException) as e:await self.b.completion(body('timeout'))
        self.assertEqual(e.exception.status_code,429);self.assertTrue(self.b.busy);self.assertFalse(self.b.uncertain)
        self.result(cmd);await first;self.assertTrue(self.b.queues[CLIENT].empty());self.assertEqual(self.b.waiting,0)
    async def test_uncertain_previous_blocks_waiter(self):
        first,cmd=await self.start();second=asyncio.create_task(self.b.completion(body('second')));await settle()
        self.result(cmd,ok=False,uncertain=True)
        with self.assertRaises(HTTPException):await first
        with self.assertRaises(HTTPException) as e:await second
        self.assertEqual(e.exception.status_code,409);self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_changed_revision_or_authorization_or_navigation_stops_waiter(self):
        for change in ('revision','permission','navigation'):
            self.setUp();first,cmd=await self.start();second=asyncio.create_task(self.b.completion(body('second')));await settle()
            if change=='revision':self.b.binding['revision']='changed'
            elif change=='permission':self.b.binding['allow_clients']=False
            else:self.b.offers[CLIENT]['context_key']='changed'
            self.result(cmd);await first
            with self.assertRaises(HTTPException):await second
            self.assertTrue(self.b.queues[CLIENT].empty());self.assertFalse(self.b.completion_gate.locked())
    async def test_http_disconnect_waiter_never_sends(self):
        first,cmd=await self.start();req=Request();second=asyncio.create_task(self.b.completion(body('second'),req));await settle();req.gone=True
        with self.assertRaises(HTTPException) as e:await asyncio.wait_for(second,1)
        self.assertEqual(e.exception.status_code,499);self.assertFalse(self.b.uncertain)
        self.result(cmd);await first;self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_http_disconnect_after_dispatch_marks_uncertain(self):
        req=Request();task=asyncio.create_task(self.b.completion(body(),req));await self.command();req.gone=True
        with self.assertRaises(HTTPException) as e:await asyncio.wait_for(task,1)
        self.assertEqual(e.exception.status_code,499);self.assertTrue(self.b.uncertain);self.assertFalse(self.b.busy)
    async def test_already_disconnected_no_dispatch(self):
        req=Request();req.gone=True
        with self.assertRaises(HTTPException):await self.b.completion(body(),req)
        self.assertTrue(self.b.queues[CLIENT].empty());self.assertFalse(self.b.uncertain)

if __name__=='__main__':unittest.main(verbosity=2)
