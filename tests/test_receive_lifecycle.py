"""No network: send wait has no duration deadline; cancellation/expiry remain explicit."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch
from conversation_bridge import ConversationBridge
from fastapi import HTTPException
CLIENT='a'*32
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
class ReceiveLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge()
        self.b.offer({'client_id':CLIENT,'context_key':KEY})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True})
    def send(self):
        return asyncio.create_task(self.b.execute('send',revision=self.b.binding['revision'],text='fixture',confirmed=True))
    async def test_send_wait_not_wrapped_in_fifteen_minute_deadline(self):
        original=asyncio.wait_for;durations=[]
        async def monitored(thing,timeout):
            durations.append(timeout)
            if timeout==900:raise AssertionError('send must not have a 900-second deadline')
            return await original(thing,timeout)
        with patch('conversation_bridge.asyncio.wait_for',monitored):
            task=self.send();await asyncio.sleep(0);self.assertFalse(task.done())
            cmd=await self.b.command(CLIENT)
            self.b.offers[CLIENT]['seen']-=60*60*5
            await asyncio.sleep(0);self.assertTrue(self.b.busy);self.assertFalse(task.done())
            self.b.result({'request_id':cmd['request_id'],'client_id':CLIENT,'context_key':KEY,'result':{'ok':True,'completed':True,'text':'late result'}})
            self.assertEqual((await task)['text'],'late result')
        self.assertNotIn(900,durations);self.assertFalse(self.b.busy)
    async def test_caller_cancel_releases_busy_and_keeps_unknown(self):
        task=self.send();await self.b.command(CLIENT);task.cancel()
        with self.assertRaises(HTTPException):await task
        self.assertFalse(self.b.busy);self.assertTrue(self.b.uncertain);self.assertFalse(self.b.pending)
    async def test_pre_dispatch_expiry_is_not_a_stuck_send(self):
        task=self.send();await asyncio.sleep(0)
        cmd=self.b.queues[CLIENT].get_nowait();cmd['expires_at']=0;self.b.queues[CLIENT].put_nowait(cmd)
        self.assertEqual(await self.b.command(CLIENT),{'kind':'idle'})
        r=await task;self.assertFalse(r['ok']);self.assertFalse(r['uncertain']);self.assertFalse(self.b.busy);self.assertFalse(self.b.uncertain)
    async def test_read_still_uses_control_request_timeout(self):
        original=asyncio.wait_for;durations=[]
        async def monitored(thing,timeout):durations.append(timeout);return await original(thing,timeout)
        with patch('conversation_bridge.asyncio.wait_for',monitored):
            task=asyncio.create_task(self.b.execute('read',revision=self.b.binding['revision']))
            cmd=await self.b.command(CLIENT)
            self.b.result({'request_id':cmd['request_id'],'client_id':CLIENT,'context_key':KEY,'result':{'ok':True}})
            await task
        self.assertIn(90,durations)
class DesktopReceiveTests(unittest.TestCase):
    def test_manual_send_has_no_reply_deadline(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"desktop"))
        from desktop.core import ConsoleCore
        core=ConsoleCore.__new__(ConsoleCore)
        core._operation=lambda work:work()
        calls=[]
        core._http=lambda *a,**kw:calls.append((a,kw))
        core.conversation('send',{'confirmed':True})
        self.assertIsNone(calls[-1][1]['timeout'])
        core.conversation('read')
        self.assertEqual(calls[-1][1]['timeout'],100)
if __name__=='__main__':unittest.main()
