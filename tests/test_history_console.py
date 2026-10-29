"""Offline history/catalog and remembered-connect checks. No browser/network processes."""
import asyncio
import json
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from fastapi import HTTPException
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'desktop'))
from conversation_bridge import ConversationBridge
from core import ConsoleCore
CLIENT='1'*32;SCOPE='a'*64;KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
ROW={'context_key':KEY,'title':'fixture'}
SOURCE={'client_id':CLIENT,'scope_id':SCOPE}
class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer(dict(SOURCE,context_key='https://arena.ai',catalog=True))
    async def respond(self,task,result,kind):
        cmd=await self.b.command(CLIENT);self.assertEqual(cmd['kind'],kind)
        self.b.result(dict(client_id=CLIENT,request_id=cmd['request_id'],context_key=cmd['context_key'],result=result))
        return await task
    async def page(self,rows=None,cursor=None,request_cursor=None):
        return await self.respond(asyncio.create_task(self.b.history(dict(SOURCE,cursor=request_cursor))),{'ok':True,'rows':rows if rows is not None else [ROW],'cursor':cursor},'catalog_list')
    async def test_homepage_lists_without_binding(self):
        p=await self.page();self.assertEqual(p['rows'][0]['context_key'],KEY);self.assertIsNone(self.b.binding);self.assertFalse(self.b.busy)
    async def test_cursor_chain(self):
        await self.page(cursor='next');p=await self.page([],None,'next');self.assertEqual(p['loaded'],1)
    async def test_reject_invented_cursor(self):
        with self.assertRaises(HTTPException):await self.b.history(dict(SOURCE,cursor='guessed'))
    async def test_scope_mismatch(self):
        with self.assertRaises(HTTPException):await self.b.history(dict(SOURCE,scope_id='b'*64))
    async def test_busy_and_uncertainty(self):
        for attr in ('busy','uncertain'):
            setattr(self.b,attr,True)
            with self.assertRaises(HTTPException):await self.b.history(SOURCE)
            setattr(self.b,attr,False)
    async def test_explicit_selection_and_page_required(self):
        for data in (dict(SOURCE,context_key=KEY),dict(SOURCE,context_key=KEY,confirmed=True)):
            with self.assertRaises(HTTPException):await self.b.connect_history(data)
    async def test_select_confirms_live_target(self):
        await self.page();task=asyncio.create_task(self.b.connect_history(dict(SOURCE,context_key=KEY,confirmed=True,allow_clients=True)))
        cmd=await self.b.command(CLIENT);self.assertEqual(cmd['kind'],'catalog_select')
        self.b.offer(dict(SOURCE,context_key=KEY,catalog=True,title='fixture'))
        self.b.result(dict(client_id=CLIENT,request_id=cmd['request_id'],context_key=cmd['context_key'],result={'ok':True,'context_key':KEY}))
        r=await task;self.assertEqual(r['binding']['context_key'],KEY);self.assertTrue(r['binding']['allow_clients'])
    async def test_changed_page_not_bound(self):
        await self.page()
        with self.assertRaises(HTTPException):await self.respond(asyncio.create_task(self.b.connect_history(dict(SOURCE,context_key=KEY,confirmed=True))),{'ok':True,'context_key':KEY},'catalog_select')
        self.assertIsNone(self.b.binding)
    async def test_errors_sanitized(self):
        with self.assertRaises(HTTPException) as ctx:await self.respond(asyncio.create_task(self.b.history(SOURCE)),{'ok':False,'error':'PRIVATE BODY'},'catalog_list')
        self.assertNotIn('PRIVATE',str(ctx.exception.detail));self.assertFalse(self.b.busy)
class ConsoleTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'.test-tmp';parent.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent);self.c=ConsoleCore(self.tmp.name);self.c._prepare=Mock(return_value=SOURCE)
        self.calls=[]
        def http(method,path,data=None,**kw):
            self.calls.append((method,path,data))
            if path.endswith('/history'):return {'rows':[ROW],'cursor':None,'source':SOURCE}
            if path.endswith('/connect'):return {'binding':ROW}
            return {}
        self.c._http=Mock(side_effect=http)
    def tearDown(self):self.tmp.cleanup()
    def test_boot_does_not_connect(self):
        self.c.boot();self.c._prepare.assert_not_called();self.c._http.assert_not_called()
    def test_first_click_only_lists(self):
        self.assertTrue(self.c.connect()['needs_selection']);self.assertFalse(any(p.endswith('/connect') for _,p,_ in self.calls));self.assertIsNone(self.c.saved)
    def test_explicit_save_and_reconnect(self):
        self.c.connect({'selection':dict(ROW,scope_id=SCOPE),'confirmed':True,'allow_clients':True})
        self.assertEqual(ConsoleCore(self.tmp.name).saved['context_key'],KEY)
        self.calls.clear();self.c.connect();self.assertTrue(any(p.endswith('/history') for _,p,_ in self.calls));self.assertTrue(any(p.endswith('/connect') for _,p,_ in self.calls));self.assertFalse(any(p.endswith('/action') for _,p,_ in self.calls))
    def test_missing_permission(self):
        with self.assertRaises(ValueError):self.c.connect({'selection':dict(ROW,scope_id=SCOPE),'confirmed':True})
        self.c._prepare.assert_not_called()
    def test_scope_mismatch_stops(self):
        self.c.saved=dict(ROW,scope_id='b'*64,allow_clients=True)
        with self.assertRaises(ValueError):self.c.connect()
        self.assertFalse(any(p.endswith('/connect') for _,p,_ in self.calls))
    def test_missing_target_stops(self):
        self.c.saved=dict(ROW,scope_id=SCOPE,allow_clients=True)
        self.c._http=Mock(return_value={'rows':[],'cursor':None})
        with self.assertRaises(ValueError):self.c.connect()
        self.assertFalse(any(x.args[1].endswith('/connect') for x in self.c._http.call_args_list))
    def test_changed_port_restarts_only_owned_bridge(self):
        process=Mock();process.poll.return_value=None;self.c.bridge_proc=process;self.c._terminate=Mock()
        self.c.save_config({'port':9191,'api_key':'fixture'})
        self.c._terminate.assert_called_once_with(process);self.assertIsNone(self.c.bridge_proc)
    def bridge_fixture(self,status,alive=True):
        d=self.c.root/'.console';d.mkdir(exist_ok=True)
        (d/'local-cdp-bridge.lock').write_text(json.dumps({'pid':12345}))
        (d/'local-cdp-bridge-status.json').write_text(json.dumps(dict(pid=12345,**status)))
        self.c._pid_running=Mock(return_value=alive);self.c._spawn=Mock(side_effect=AssertionError('must not spawn'))
    def test_old_bridge_blocks_even_with_stale_timestamp(self):
        self.bridge_fixture({'ready':True,'updated_at':0,'gateway_port':9090})
        with self.assertRaisesRegex(ValueError,'旧版本本机对话桥仍在运行'):self.c._bridge()
        self.c._spawn.assert_not_called()
    def test_modern_fresh_bridge_reused(self):
        self.bridge_fixture({'ready':True,'protocol':2,'updated_at':time.time()*1000,'gateway_port':9090})
        self.c._bridge();self.c._spawn.assert_not_called()
    def test_stale_modern_bridge_reports_heartbeat(self):
        self.bridge_fixture({'ready':True,'protocol':2,'updated_at':0,'gateway_port':9090})
        with self.assertRaisesRegex(ValueError,'心跳'):self.c._bridge()
    def test_unverifiable_lock_fails_closed(self):
        self.bridge_fixture({},None)
        with self.assertRaisesRegex(ValueError,'无法只读核验'):self.c._bridge()
        self.assertTrue((self.c.root/'.console/local-cdp-bridge.lock').exists())
    def test_other_port_bridge_reports_conflict(self):
        self.bridge_fixture({'ready':True,'protocol':2,'updated_at':time.time()*1000,'gateway_port':9191})
        with self.assertRaisesRegex(ValueError,'旧端口'):self.c._bridge()
    def test_workspace_start_only_starts_gateway(self):
        self.c.start=Mock();self.c._bridge=Mock();self.c.status=Mock(return_value={'compatible':True,'version':'0.5.0','send_mode_supported':True})
        self.c.workspace_start();self.c.start.assert_called_once();self.c._bridge.assert_not_called()
    def test_workspace_rejects_old_gateway(self):
        self.c.start=Mock();self.c.status=Mock(return_value={'compatible':True,'version':'0.4.0'})
        with self.assertRaisesRegex(ValueError,'旧网关'):self.c.workspace_start()
    def test_compat_bridge_requires_explicit_target_confirmation(self):
        self.c.start=Mock()
        with self.assertRaises(ValueError):self.c.compat_bridge({'name':'arbitrary fixture','cdp_port':36938})
        self.c.start.assert_not_called()
    def test_mutex_and_config_validation(self):
        self.c.test_lock.acquire()
        with self.assertRaises(ValueError):self.c.history()
        self.c.test_lock.release()
        for data in ({'port':80,'api_key':'x'},{'port':True,'api_key':'x'},{'port':9090,'api_key':'bad key'}):
            with self.assertRaises(ValueError):self.c.validate_config(data)
if __name__=='__main__':unittest.main()
