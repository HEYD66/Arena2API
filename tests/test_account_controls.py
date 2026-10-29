"""Fixture-only account switches, queue guards and unique model mappings."""
import asyncio,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_source_router as fixture
A,B,K1,K2=fixture.A,fixture.B,fixture.K1,fixture.K2
from source_router import SourceRouter
from fastapi import HTTPException
class AccountControlsTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.RouterTests.setUp
    tearDown=fixture.RouterTests.tearDown
    command=fixture.RouterTests.command
    result=fixture.RouterTests.result
    page=fixture.RouterTests.page
    bind=fixture.RouterTests.bind
    start=fixture.RouterTests.start
    async def wait_until(self,predicate):
        async def wait():
            while not predicate():await asyncio.sleep(0)
        await asyncio.wait_for(wait(),3)
    async def toggle(self,on):return await self.r.account_control({'account_key':A['account_key'],'enabled':on,'confirmed':True})
    async def test_gate_preserves_conversation_choices(self):
        await self.bind(A,K1,'coding');await self.bind(A,K2,'writing');await self.toggle(False)
        self.assertTrue(all(r['enabled'] for r in self.r.routes.values()));self.assertEqual(self.r.advertised_models(),[])
        with self.assertRaises(HTTPException):await self.start('coding')
        await self.toggle(True);self.assertEqual(self.r.advertised_models(),['coding','writing'])
    async def test_gate_persistence_does_not_restore_conversations(self):
        await self.bind(A,K1,'coding');await self.toggle(False);r=SourceRouter(self.path)
        self.assertFalse(r.account_enabled(A['account_key']));self.assertFalse(r.routes['coding']['enabled'])
        await r.account_control({'account_key':A['account_key'],'enabled':True,'confirmed':True})
        self.assertFalse(r.routes['coding']['enabled'])
    async def test_account_pause_preserves_inflight_result(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A);await self.toggle(False)
        self.assertTrue(self.r.engines['coding'].busy);self.result(A,cmd,{'ok':True,'completed':True,'text':'existing request completes'})
        self.assertIn('existing request completes',''.join([s async for s in it]));self.assertFalse(self.r.routes['coding']['pending_review'])
    async def test_unclaimed_command_rechecks_account_gate(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');await self.wait_until(lambda:not self.r.queues[A['client_id']].empty());await self.toggle(False)
        self.assertEqual((await self.command(A))['kind'],'idle')
        self.assertIn('error',''.join([s async for s in it]));self.assertFalse(self.r.routes['coding']['pending_review'])
    async def test_queued_request_rechecks_account_gate(self):
        await self.bind(A,K1,'coding');await self.bind(A,K2,'writing');first=await self.start('coding');cmd=await self.command(A);second=await self.start('writing');await self.wait_until(lambda:self.r.engines['writing'].waiting>0)
        await self.toggle(False);self.result(A,cmd,{'ok':True,'completed':True,'text':'first'})
        async for _ in first:pass
        self.assertIn('error',''.join([s async for s in second]));self.assertTrue(self.r.queues[A['client_id']].empty())
    async def test_other_account_unaffected(self):
        await self.bind(A,K1,'coding');await self.bind(B,K2,'writing');await self.toggle(False);self.assertEqual(self.r.advertised_models(),['writing'])
    async def test_unknown_or_unconfirmed_account_rejected(self):
        for data in [{'account_key':'f'*64,'enabled':False,'confirmed':True},{'account_key':A['account_key'],'enabled':False},{'account_key':A['account_key'],'enabled':'yes','confirmed':True}]:
            with self.assertRaises(HTTPException):await self.r.account_control(data)
    async def test_corrupt_preferences_fail_closed(self):
        self.r.account_preferences_path.write_text('broken');r=SourceRouter(self.path);r.offer(A)
        self.assertFalse(r.account_enabled(A['account_key']))
        with self.assertRaises(HTTPException):await r.account_control({'account_key':A['account_key'],'enabled':True,'confirmed':True})
        self.assertEqual(r.account_preferences_path.read_text(),'broken')
    async def test_rename_preserves_identity_and_callbacks(self):
        await self.bind(A,K1,'coding');before=dict(self.r.routes['coding']);await self.r.rename({'alias':'coding','new_alias':'coding-v2','confirmed':True})
        self.assertNotIn('coding',self.r.routes)
        for k in ['client_id','account_key','context_key','allow_clients']:self.assertEqual(before[k],self.r.routes['coding-v2'][k])
        it=await self.start('coding-v2');cmd=await self.command(A);self.assertTrue(self.r.routes['coding-v2']['pending_review']);self.result(A,cmd,{'ok':True,'completed':True,'text':'renamed'})
        async for _ in it:pass
        self.assertFalse(self.r.routes['coding-v2']['pending_review'])
    async def test_rename_duplicates_and_pending_are_rejected(self):
        await self.bind(A,K1,'coding');await self.bind(B,K2,'writing')
        for name in ['writing','Bad Name','../oops']:
            with self.assertRaises(HTTPException):await self.r.rename({'alias':'coding','new_alias':name,'confirmed':True})
        self.r.routes['coding']['pending_review']=True
        with self.assertRaises(HTTPException):await self.r.rename({'alias':'coding','new_alias':'new','confirmed':True})
        self.assertTrue(self.r.routes['coding']['pending_review'])
    async def test_rename_failure_rolls_back(self):
        await self.bind(A,K1,'coding')
        with patch.object(self.r,'save',side_effect=OSError('fixture')):
            with self.assertRaises(HTTPException):await self.r.rename({'alias':'coding','new_alias':'new','confirmed':True})
        self.assertIn('coding',self.r.routes);self.assertNotIn('new',self.r.routes);self.r.engines['coding'].request_guard();self.assertEqual(self.r.engines['coding'].model_alias,'coding')
    async def test_account_save_failure_preserves_state(self):
        with patch('pathlib.Path.replace',side_effect=OSError('fixture')):
            with self.assertRaises(HTTPException):await self.toggle(False)
        self.assertTrue(self.r.account_enabled(A['account_key']))
    async def test_real_http_account_gate_and_models(self):
        import httpx,server
        await self.bind(A,K1,'coding')
        with patch.object(server,'source_router',self.r):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://fixture',headers={'Authorization':'Bearer '+server.API_KEY}) as client:
                self.assertTrue((await client.get('/health')).json()['account_controls_supported'])
                self.assertEqual((await client.get('/v1/models')).json()['data'][0]['id'],'coding')
                response=await client.post('/v1/workspace/account',json={'account_key':A['account_key'],'enabled':False,'confirmed':True})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.get('/v1/models')).json()['data'],[])
                response=await client.post('/v1/chat/completions',json={'model':'coding','messages':[{'role':'user','content':'fixture, must never dispatch'}]})
                self.assertEqual(response.status_code,403);self.assertTrue(self.r.queues[A['client_id']].empty())
    async def test_real_http_rename_and_no_legacy_fallback(self):
        import httpx,server
        await self.bind(A,K1,'coding')
        with patch.object(server,'source_router',self.r):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://fixture',headers={'Authorization':'Bearer '+server.API_KEY}) as client:
                response=await client.post('/v1/workspace/rename',json={'alias':'coding','new_alias':'code-review','confirmed':True})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.get('/v1/models')).json()['data'][0]['id'],'code-review')
                await self.toggle(False);self.r.routes.clear();self.r.sources.clear()
                self.assertEqual((await client.get('/v1/models')).json()['data'],[])
                response=await client.post('/v1/chat/completions',json={'model':'arena-bound','messages':[{'role':'user','content':'fixture, no legacy bypass'}]})
                self.assertEqual(response.status_code,409)
if __name__=='__main__':unittest.main()
