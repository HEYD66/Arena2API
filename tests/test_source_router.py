"""Offline multi-account routing: no browser, network or account requests."""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from fastapi import HTTPException
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from source_router import SourceRouter
A={'client_id':'a'*32,'session':'1'*32,'account_key':'a'*64,'label':'fixture A','source_label':'Browser A'}
B={'client_id':'b'*32,'session':'2'*32,'account_key':'b'*64,'label':'fixture B','source_label':'Browser B'}
K1='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
K2='https://arena.ai/agent/22222222-2222-4222-8222-222222222222'
class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        d=ROOT/'.test-tmp';d.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=d)
        self.path=Path(self.tmp.name)/'routes.json';self.r=SourceRouter(self.path);self.r.offer(A);self.r.offer(B)
    def tearDown(self):self.tmp.cleanup()
    async def command(self,s):return await asyncio.wait_for(self.r.command(s['client_id']),1)
    def result(self,s,cmd,result=None,progress=None):
        data=dict(s,request_id=cmd['request_id'],context_key=cmd['context_key'])
        if progress is not None:data['progress']=progress
        else:data['result']=result
        return self.r.result(data)
    async def page(self,s,key):
        t=asyncio.create_task(self.r.history(s));cmd=await self.command(s)
        self.assertEqual(cmd['kind'],'catalog_list');self.result(s,cmd,{'ok':True,'rows':[{'context_key':key,'title':'fixture'}],'cursor':None});return await t
    async def bind(self,s,key,alias):
        await self.page(s,key)
        t=asyncio.create_task(self.r.bind(dict(s,context_key=key,alias=alias,confirmed=True,allow_clients=True)));cmd=await self.command(s)
        self.assertEqual(cmd['kind'],'catalog_select');self.result(s,cmd,{'ok':True,'context_key':key});await t
    async def start(self,alias):
        response=await self.r.completion({'model':alias,'stream':True,'messages':[{'role':'user','content':'fixture'}]},None)
        it=response.body_iterator;first=await anext(it);self.assertIn('"model": "'+alias+'"',first);return it
    async def test_two_sources_discovered_without_catalog_reads(self):
        self.assertEqual(len(self.r.status()['sources']),2);self.assertTrue(all(q.empty() for q in self.r.queues.values()));self.assertFalse(self.path.exists())
    async def test_display_name_not_identity(self):
        with self.assertRaises(HTTPException):self.r.offer(dict(A,account_key='username'))
    async def test_unverified_source_visible_but_cannot_read(self):
        self.r.offer(dict(A,account_key=None,identity_status='unavailable'))
        self.assertIsNone(self.r.sources[A['client_id']]['account_key'])
        with self.assertRaises(HTTPException):await self.r.history(dict(A,account_key=None))
    async def test_bind_requires_history_and_authorization(self):
        for data in (dict(A,alias='coding',context_key=K1),dict(A,alias='coding',context_key=K1,confirmed=True,allow_clients=True)):
            with self.assertRaises(HTTPException):await self.r.bind(data)
    async def test_two_accounts_route_independently(self):
        await self.bind(A,K1,'coding');await self.bind(B,K2,'writing')
        x=await self.start('coding');y=await self.start('writing');ca,cb=await asyncio.gather(self.command(A),self.command(B))
        self.assertEqual(ca['context_key'],K1);self.assertEqual(cb['context_key'],K2)
        self.assertEqual(ca['account_key'],A['account_key']);self.assertEqual(cb['account_key'],B['account_key'])
        self.result(A,ca,progress={'seq':1,'delta':'A reply'});self.result(B,cb,progress={'seq':1,'delta':'B reply'})
        self.result(A,ca,{'ok':True,'completed':True,'text':'A reply'});self.result(B,cb,{'ok':True,'completed':True,'text':'B reply'})
        ra=''.join([v async for v in x]);rb=''.join([v async for v in y]);self.assertIn('A reply',ra);self.assertNotIn('B reply',ra);self.assertIn('B reply',rb)
    async def test_same_profile_serializes_different_conversations(self):
        await self.bind(A,K1,'coding');await self.bind(A,K2,'writing')
        x=await self.start('coding');ca=await self.command(A);y=await self.start('writing');await asyncio.sleep(.01)
        self.assertTrue(self.r.queues[A['client_id']].empty())
        self.result(A,ca,{'ok':True,'completed':True,'text':'one'});await anext(x)
        async for _ in x:pass
        cb=await self.command(A);self.assertEqual(cb['context_key'],K2);self.result(A,cb,{'ok':True,'completed':True,'text':'two'})
        async for _ in y:pass
    async def test_wrong_account_and_session_rejected(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A)
        for wrong in (dict(A,account_key=B['account_key']),dict(A,session='f'*32),B):
            with self.assertRaises(HTTPException):self.result(wrong,cmd,{'ok':True,'text':'foreign'})
        self.result(A,cmd,{'ok':True,'completed':True,'text':'own'})
        async for _ in it:pass
    async def test_account_switch_disables_binding(self):
        await self.bind(A,K1,'coding');self.r.offer(dict(A,account_key='c'*64))
        self.assertFalse(self.r.routes['coding']['enabled'])
        with self.assertRaises(HTTPException):await self.start('coding')
    async def test_restart_requires_explicit_restore(self):
        await self.bind(A,K1,'coding');self.r.sources[A['client_id']]['seen']=time.time()-20
        self.r.offer(dict(A,session='f'*32));self.assertFalse(self.r.routes['coding']['enabled'])
    async def test_overlapping_profile_clone_rejected(self):
        with self.assertRaises(HTTPException):self.r.offer(dict(A,session='f'*32))
        self.assertEqual(self.r.sources[A['client_id']]['session'],A['session'])
    async def test_offline_source_not_routed(self):
        await self.bind(A,K1,'coding');self.r.sources[A['client_id']]['seen']=time.time()-20
        with self.assertRaises(HTTPException):await self.start('coding')
    async def test_no_alias_retarget(self):
        await self.bind(A,K1,'coding');await self.page(B,K2)
        with self.assertRaises(HTTPException):await self.r.bind(dict(B,alias='coding',context_key=K2,confirmed=True,allow_clients=True))
    async def test_same_physical_target_not_aliased_twice(self):
        await self.bind(A,K1,'coding');other=dict(B,account_key=A['account_key']);self.r.offer(other);await self.page(other,K1)
        with self.assertRaises(HTTPException):await self.r.bind(dict(other,alias='duplicate',context_key=K1,confirmed=True,allow_clients=True))
    async def test_persistence_is_disabled_on_restart(self):
        await self.bind(A,K1,'coding');new=SourceRouter(self.path)
        self.assertFalse(new.routes['coding']['enabled']);self.assertFalse(new.engines)
        self.assertNotIn('session',self.path.read_text());self.assertNotIn('messages',self.path.read_text())
    async def test_explicit_disable_and_delete(self):
        await self.bind(A,K1,'coding');await self.r.control({'alias':'coding','action':'disable','confirmed':True})
        self.assertFalse(self.r.routes['coding']['enabled']);await self.r.control({'alias':'coding','action':'delete','confirmed':True});self.assertFalse(self.r.routes)
    async def test_uncertain_route_cannot_be_replaced(self):
        await self.bind(A,K1,'coding');self.r.engines['coding'].uncertain=True
        with self.assertRaises(HTTPException):await self.r.control({'alias':'coding','action':'delete','confirmed':True})
    async def test_restore_rechecks_and_sends_no_chat(self):
        await self.bind(A,K1,'coding')
        await self.r.control({'alias':'coding','action':'disable','confirmed':True})
        task=asyncio.create_task(self.r.restore({'alias':'coding','confirmed':True}))
        cmd=await self.command(A);self.assertEqual(cmd['kind'],'catalog_list')
        self.result(A,cmd,{'ok':True,'rows':[{'context_key':K1,'title':'fixture'}],'cursor':None})
        cmd=await self.command(A);self.assertEqual(cmd['kind'],'catalog_select');self.result(A,cmd,{'ok':True,'context_key':K1})
        await task;self.assertTrue(self.r.routes['coding']['enabled']);self.assertTrue(self.r.queues[A['client_id']].empty())
    async def test_request_started_during_rebind_does_not_lose_engine(self):
        await self.bind(A,K1,'coding');old=self.r.engines['coding']
        task=asyncio.create_task(self.r.bind(dict(A,alias='coding',context_key=K1,confirmed=True,allow_clients=True)))
        cmd=await self.command(A);old.busy=True
        self.result(A,cmd,{'ok':True,'context_key':K1})
        with self.assertRaises(HTTPException):await task
        self.assertIs(self.r.engines['coding'],old);old.busy=False
    async def test_models_and_auth_use_configured_aliases(self):
        import httpx,server
        from unittest.mock import patch
        await self.bind(A,K1,'coding')
        with patch.object(server,'source_router',self.r):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
                self.assertEqual((await c.get('/v1/workspace/status')).status_code,401)
                h={'Authorization':'Bearer '+server.API_KEY}
                models=(await c.get('/v1/models',headers=h)).json()['data']
                self.assertEqual([v['id'] for v in models],['coding'])
                self.assertNotIn('account_key',str(models))
                response=await c.post('/v1/chat/completions',headers=h,json={'model':'arena-bound','messages':[{'role':'user','content':'fixture'}]})
                self.assertEqual(response.status_code,409)
    async def test_queued_old_account_command_not_delivered_to_new_account(self):
        await self.bind(A,K1,'coding');it=await self.start('coding')
        for _ in range(8):await asyncio.sleep(0)
        self.r.offer(dict(A,account_key='c'*64));cmd=await self.command(A)
        self.assertEqual(cmd['kind'],'idle');frames=''.join([v async for v in it]);self.assertIn('"error"',frames)
    async def test_send_guard_is_persisted_before_dispatch_and_cleared_on_completion(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A)
        self.assertTrue(json.loads(self.path.read_text())[0]['pending_review'])
        self.assertFalse(self.r.status()['routes'][0]['uncertain'])  # active, not a completed unknown request
        self.result(A,cmd,{'ok':True,'completed':True,'text':'done'})
        async for _ in it:pass
        self.assertFalse(json.loads(self.path.read_text())[0]['pending_review'])
    async def test_restart_preserves_unfinished_guard_and_refuses_restore_delete(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');await self.command(A)
        await it.aclose()
        new=SourceRouter(self.path);new.offer(A)
        self.assertTrue(new.status()['routes'][0]['uncertain'])
        with self.assertRaises(HTTPException):await new.restore({'alias':'coding','confirmed':True})
        with self.assertRaises(HTTPException):await new.control({'alias':'coding','action':'delete','confirmed':True})
        self.assertTrue(new.queues[A['client_id']].empty())
    async def test_guard_write_failure_prevents_browser_command(self):
        from unittest.mock import patch
        await self.bind(A,K1,'coding')
        with patch.object(self.r,'save',side_effect=OSError('fixture')):
            it=await self.start('coding');frames=''.join([v async for v in it])
        self.assertIn('"error"',frames);self.assertTrue(self.r.queues[A['client_id']].empty())
        self.assertTrue(self.r.engines['coding'].uncertain);self.assertFalse(self.r.engines['coding'].busy)
    async def test_same_worker_confirmed_check_clears_durable_guard(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A)
        self.result(A,cmd,{'ok':False,'uncertain':True})
        async for _ in it:pass
        task=asyncio.create_task(self.r.control({'alias':'coding','action':'check','confirmed':True}))
        check=await self.command(A);self.assertEqual(check['kind'],'check')
        self.result(A,check,{'ok':True,'resolved':True});await task
        self.assertFalse(json.loads(self.path.read_text())[0]['pending_review'])
    async def test_unresolved_check_falls_back_to_manual_review(self):
        # 扩展丢失本轮记录时，自动核对返回 resolved=False；应转人工核对而不是永远卡住
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A)
        self.result(A,cmd,{'ok':False,'uncertain':True,'error_code':'EXECUTION_SCOPE'})
        async for _ in it:pass
        task=asyncio.create_task(self.r.control({'alias':'coding','action':'check','confirmed':True}))
        check=await self.command(A);self.result(A,check,{'ok':True,'resolved':False})
        result=await task
        self.assertTrue(result['review_required']);self.assertIn('review_token',result)
        self.assertTrue(json.loads(self.path.read_text())[0]['pending_review'])
        ack=await self.r.control({'alias':'coding','action':'acknowledge_review','confirmed':True,'acknowledged':True,'review_token':result['review_token']})
        self.assertTrue(ack['review_acknowledged']);self.assertFalse(ack['enabled']);self.assertFalse(ack['replayed'])
        saved=json.loads(self.path.read_text())[0]
        self.assertFalse(saved['pending_review']);self.assertFalse(self.r.routes['coding']['enabled'])
    async def test_incomplete_final_result_keeps_guard(self):
        await self.bind(A,K1,'coding');it=await self.start('coding');cmd=await self.command(A)
        self.result(A,cmd,{'ok':True,'text':'unconfirmed'})
        async for _ in it:pass
        self.assertTrue(self.r.engines['coding'].uncertain)
        self.assertTrue(json.loads(self.path.read_text())[0]['pending_review'])
    async def test_bad_routes_never_overwritten(self):
        self.path.write_text('[bad fixture json')
        new=SourceRouter(self.path)
        with self.assertRaises(HTTPException):new.save()
        self.assertTrue(new.load_error);self.assertEqual(new.routes,{})
        self.assertEqual(self.path.read_text(),'[bad fixture json')
    async def test_gateway_mode_provider_applies_to_each_alias_without_rebinding(self):
        from unittest.mock import AsyncMock
        mode=['normal'];self.r.send_mode_provider=lambda:mode[0]
        await self.bind(A,K1,'coding');await self.bind(B,K2,'writing')
        for alias in ('coding','writing'):
            self.r.engines[alias].execute=AsyncMock(return_value={'ok':True,'completed':True,'text':'fixture'})
        messages=[{'role':'user','content':'old fixture'},{'role':'assistant','content':'old reply'},{'role':'user','content':'latest fixture'}]
        await self.r.completion({'model':'coding','messages':messages},None)
        self.assertEqual(self.r.engines['coding'].execute.call_args.kwargs['text'],'latest fixture')
        mode[0]='full'
        await self.r.completion({'model':'writing','messages':messages},None)
        self.assertIn('old fixture',self.r.engines['writing'].execute.call_args.kwargs['text'])
        self.assertTrue(all(r['enabled'] for r in self.r.routes.values()))
    async def test_history_permission_is_explicit_boolean_metadata_only(self):
        self.r.offer(dict(A,history_enabled='true'))
        self.assertFalse(self.r.sources[A['client_id']]['history_enabled'])
        self.r.offer(dict(A,history_enabled=True))
        self.assertTrue(self.r.sources[A['client_id']]['history_enabled'])
        self.assertTrue(self.r.queues[A['client_id']].empty())
if __name__=='__main__':unittest.main()
