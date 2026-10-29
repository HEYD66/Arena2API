"""No live sockets/accounts: explicit binding and existing-conversation routing."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from conversation_bridge import ConversationBridge

CLIENT='a'*32
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
OTHER='https://arena.ai/agent/22222222-2222-4222-8222-222222222222'

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge()
        self.b.offer({'client_id':CLIENT,'context_key':KEY,'title':'fixture'})
        self.bind()
    def bind(self, clients=False):
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':clients})
        self.rev=self.b.binding['revision']
    async def request(self,kind='read',result=None):
        task=asyncio.create_task(self.b.execute(kind,revision=self.rev,text='fixture',confirmed=True))
        cmd=await self.b.command(CLIENT)
        self.b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':result or {'ok':True,'text':'OK'}})
        return await task,cmd
    def test_metadata_only(self):
        self.assertNotIn('messages',self.b.status());self.assertFalse(self.b.busy)
        self.assertTrue(self.b.queues[CLIENT].empty())
    def test_offer_rejects_external(self):
        with self.assertRaises(HTTPException):self.b.offer({'client_id':CLIENT,'context_key':'https://example.com/agent/x'})
    def test_binding_requires_confirmation(self):
        with self.assertRaises(HTTPException):self.b.bind({'client_id':CLIENT,'context_key':KEY})
    def test_bind_rejects_changed_offer(self):
        with self.assertRaises(HTTPException):self.b.bind({'client_id':CLIENT,'context_key':OTHER,'confirmed':True})
    async def test_read_no_send(self):
        result,cmd=await self.request();self.assertTrue(result['ok']);self.assertEqual(cmd['kind'],'read');self.assertEqual(cmd['text'],'')
    async def test_send_once_same_context(self):
        _,cmd=await self.request('send');self.assertEqual(cmd['context_key'],KEY);self.assertEqual(cmd['text'],'fixture');self.assertTrue(self.b.queues[CLIENT].empty())
    async def test_send_requires_confirmation(self):
        with self.assertRaises(HTTPException):await self.b.execute('send',revision=self.rev,text='hello')
    async def test_old_revision_rejected(self):
        with self.assertRaises(HTTPException):await self.b.execute('read',revision='stale')
    async def test_navigation_no_follow(self):
        self.b.offer({'client_id':CLIENT,'context_key':OTHER})
        self.assertFalse(self.b.live())
        with self.assertRaises(HTTPException):await self.b.execute('send',revision=self.rev,text='hello',confirmed=True)
        self.assertEqual(self.b.binding['context_key'],KEY)
    async def test_expired_offer(self):
        self.b.offers[CLIENT]['seen']=0
        with self.assertRaises(HTTPException):await self.b.execute('read',revision=self.rev)
    async def test_cross_instance_result_rejected(self):
        task=asyncio.create_task(self.b.execute('read',revision=self.rev));cmd=await self.b.command(CLIENT)
        with self.assertRaises(HTTPException):self.b.result({'client_id':'b'*32,'request_id':cmd['request_id'],'context_key':KEY,'result':{'ok':True}})
        self.b.result({'client_id':CLIENT,'request_id':cmd['request_id'],'context_key':KEY,'result':{'ok':True}});await task
    async def test_busy_rebind_rejected(self):
        task=asyncio.create_task(self.b.execute('read',revision=self.rev));cmd=await self.b.command(CLIENT)
        with self.assertRaises(HTTPException):self.bind()
        with self.assertRaises(HTTPException):await self.b.execute('send',revision=self.rev,text='x',confirmed=True)
        self.b.result({'client_id':CLIENT,'request_id':cmd['request_id'],'context_key':KEY,'result':{'ok':True}});await task
    async def test_uncertain_blocks_resend(self):
        await self.request('send',{'ok':False,'uncertain':True})
        with self.assertRaises(HTTPException):await self.b.execute('send',revision=self.rev,text='x',confirmed=True)
        with self.assertRaises(HTTPException):self.bind()
        await self.request('check',{'ok':True,'resolved':False});self.assertTrue(self.b.uncertain)
        await self.request('check',{'ok':True,'resolved':True});self.assertFalse(self.b.uncertain)
    async def test_preflight_failure_not_uncertain(self):
        await self.request('send',{'ok':False,'uncertain':False});self.assertFalse(self.b.uncertain)
    async def test_cancelled_send_locks(self):
        task=asyncio.create_task(self.b.execute('send',revision=self.rev,text='x',confirmed=True));await self.b.command(CLIENT);task.cancel()
        with self.assertRaises(HTTPException):await task
        self.assertTrue(self.b.uncertain);self.assertFalse(self.b.busy)
    async def test_bound_route_compacts_all_228_tools_before_dispatch(self):
        from test_tool_compaction import fixtures
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':True,'text':'ok'})
        tools=fixtures()
        result=await self.b.completion({'model':'arena-bound','tools':tools,'messages':[{'role':'user','content':'hi'}]})
        prompt=self.b.execute.call_args.kwargs['text']
        self.assertLessEqual(len(prompt.encode('utf-16-le'))//2,200000)
        self.assertEqual(prompt.count('\n  parameters: '),228)
        self.assertEqual(result['choices'][0]['message']['content'],'ok')

    async def test_large_client_context_within_native_ceiling(self):
        self.b.send_mode_provider=lambda: 'full'
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':True,'text':'ok'})
        body={'model':'arena-bound','messages':[{'role':'system','content':'x'*115000},{'role':'user','content':'hi'}],
              'tools':[{'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}]}
        await self.b.completion(body)
        self.assertGreater(len(self.b.execute.call_args.kwargs['text']),100000)
        self.assertIn('x'*115000,self.b.execute.call_args.kwargs['text'])

    async def test_budget_error_counts_only_no_dispatch(self):
        from openai_compat import ProtocolError
        self.bind(True);self.b.execute=AsyncMock()
        secret='fixture-private-content'
        with self.assertRaises(ProtocolError) as ctx:
            await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':secret+'x'*200001}],
                'tools':[{'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}]})
        self.assertIn('limit=200000',str(ctx.exception))
        self.assertIn('tool_schema=',str(ctx.exception))
        self.assertIn('history=',str(ctx.exception))
        self.assertNotIn(secret,str(ctx.exception))
        self.b.execute.assert_not_awaited()

    async def test_non_bmp_matches_javascript_length(self):
        from openai_compat import ProtocolError
        self.bind(True);self.b.execute=AsyncMock()
        with self.assertRaises(ProtocolError):
            await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'😀'*100001}]})
        self.b.execute.assert_not_awaited()

    async def test_clients_default_denied(self):
        with self.assertRaises(HTTPException):await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'hi'}]})
    async def test_completion_without_model_catalog(self):
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':True,'text':'fixture answer'})
        r=await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'hi'}]})
        self.assertEqual(r['choices'][0]['message']['content'],'fixture answer')
        self.assertEqual(self.b.execute.call_args.kwargs['revision'],self.rev)
    async def test_buffered_stream(self):
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':True,'text':'fixture answer'})
        r=await self.b.completion({'model':'arena-bound','stream':True,'messages':[{'role':'user','content':'hi'}]})
        data=''.join([x async for x in r.body_iterator]);self.assertIn('[DONE]',data);self.assertIn('fixture answer',data)
    async def test_function_calls_use_existing_parser(self):
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':True,'text':'<tool_call>{"name":"echo","arguments":{"text":"ok"}}</tool_call>'})
        r=await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],
           'tools':[{'type':'function','function':{'name':'echo','parameters':{'type':'object','properties':{'text':{'type':'string'}}}}}]})
        self.assertEqual(r['choices'][0]['finish_reason'],'tool_calls')
    async def test_unconfirmed_reply_no_success(self):
        self.bind(True);self.b.execute=AsyncMock(return_value={'ok':False,'uncertain':True})
        with self.assertRaises(HTTPException):await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'hi'}]})
    def test_restart_forgets_binding(self):
        self.assertIsNone(ConversationBridge().binding)
    def test_unbind_does_not_delete_remote(self):
        self.b.bind({'confirmed':True,'unbind':True});self.assertIsNone(self.b.binding);self.assertTrue(self.b.queues[CLIENT].empty())

class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_advertises_alias_without_binding_or_account_calls(self):
        import httpx
        import server
        from unittest.mock import patch
        bridge=ConversationBridge()
        with patch.object(server,'conversation_bridge',bridge), patch.object(server,'verify_api_key'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://fixture') as client:
                response=await client.get('/v1/models')
        self.assertEqual(response.status_code,200)
        rows=response.json()['data']
        self.assertEqual(sum(row['id']=='arena-bound' for row in rows),1)
        self.assertIsNone(bridge.binding)
        self.assertFalse(bridge.pending)

    async def test_bound_alias_bypasses_legacy_model_and_token_path(self):
        import httpx
        import server
        from unittest.mock import patch
        bridge=ConversationBridge()
        bridge.offer({'client_id':CLIENT,'context_key':KEY})
        bridge.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
        bridge.execute=AsyncMock(return_value={'ok':True,'text':'bound fixture reply'})
        with patch.object(server,'conversation_bridge',bridge), patch.object(server,'verify_api_key'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://fixture') as client:
                r=await client.post('/v1/chat/completions',json={'model':'arena-bound','messages':[{'role':'user','content':'fixture'}]})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['choices'][0]['message']['content'],'bound fixture reply')


if __name__=='__main__':unittest.main(verbosity=2)
