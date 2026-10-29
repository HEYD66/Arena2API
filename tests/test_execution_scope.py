"""Offline scope attestation, prompt boundaries and receive deadline contracts."""
import asyncio
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from fastapi import HTTPException
from conversation_bridge import ConversationBridge
from openai_compat import normalize_request, render_agent_prompt
from tool_reference import render_reference_prompt

KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
CLIENT='e'*32
TOOL={'type':'function','function':{'name':'fixture','parameters':{'type':'object'}}}
def body(**extra):return dict(model='arena-bound',messages=[{'role':'user','content':'create HTML locally'}],tools=[TOOL],**extra)

class ScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
    def finish(self,cmd,**extra):
        self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':dict(ok=True,completed=True,text='done',**extra)})
    async def test_old_extension_cannot_claim_local_success(self):
        t=asyncio.create_task(self.b.completion(body()))
        cmd=await self.b.command(CLIENT);self.assertTrue(cmd['client_execution']);self.assertFalse(cmd['reference_reads'])
        self.finish(cmd)
        with self.assertRaises(HTTPException) as e:await t
        self.assertIn('CLIENT_SCOPE_UNVERIFIED',str(e.exception.detail));self.assertTrue(self.b.uncertain)
        with self.assertRaises(HTTPException):await self.b.completion(body())
        self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_verified_new_extension_success(self):
        t=asyncio.create_task(self.b.completion(body()))
        self.finish(await self.b.command(CLIENT),execution_scope_verified=True)
        self.assertEqual((await t)['choices'][0]['message']['content'],'done');self.assertFalse(self.b.uncertain)
    async def test_reference_policy_is_per_request(self):
        for reference in [True,False,True]:
            self.b.tool_reference_provider=lambda:reference
            t=asyncio.create_task(self.b.completion(body()))
            cmd=await self.b.command(CLIENT);self.assertEqual(cmd['reference_reads'],reference)
            self.finish(cmd,execution_scope_verified=True);await t
    async def test_stream_old_extension_emits_error_not_tool_calls(self):
        r=await self.b.completion(body(stream=True));it=r.body_iterator;await anext(it)
        cmd=await self.b.command(CLIENT)
        self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'completed':True,'text':'<tool_call>{"name":"fixture","arguments":{}}</tool_call>'}})
        tail=''.join([x async for x in it]);self.assertIn('CLIENT_SCOPE_UNVERIFIED',tail)
        self.assertNotIn('"finish_reason": "tool_calls"',tail);self.assertNotIn('"finish_reason": "stop"',tail)
        self.assertTrue(self.b.uncertain)
    async def test_gateway_send_has_no_total_deadline_and_plain_client_compatibility(self):
        real=asyncio.wait_for;deadlines=[]
        async def watched(awaitable,timeout):
            deadlines.append(timeout);return await real(awaitable,timeout)
        with patch('conversation_bridge.asyncio.wait_for',watched):
            request=body();request.pop('tools')
            t=asyncio.create_task(self.b.completion(request));cmd=await self.b.command(CLIENT)
            self.assertFalse(cmd['client_execution']);self.finish(cmd);await t
        self.assertNotIn(900,deadlines)
    def test_both_renderers_state_execution_boundary(self):
        b,p,_=normalize_request(body())
        for text in [render_agent_prompt(b['messages'],p,200000),render_reference_prompt(b['messages'],p,200000)]:
            self.assertIn('EXECUTION DOMAIN',text);self.assertIn('NOT a client-local deliverable',text)
            self.assertIn('MUST use the declared CLIENT tools',text)
        ref=render_reference_prompt(b['messages'],p,200000)
        self.assertIn('ONLY permitted native operations',ref);self.assertIn('limit 1..300',ref)
        self.assertNotIn('file/search capability',ref)

if __name__=='__main__':unittest.main()
