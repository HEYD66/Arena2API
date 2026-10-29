"""Gateway send-mode tests: fixture files and mocked commands only."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'desktop'))
from desktop.send_settings import SendModeSettings
from conversation_bridge import ConversationBridge
from core import ConsoleCore
from fastapi import HTTPException
from openai_compat import ProtocolError

CLIENT='a'*32
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
TOOL={'type':'function','function':{'name':'fixture_echo','parameters':{'type':'object','properties':{'x':{'type':'string'}}}}}
HISTORY=[{'role':'system','content':'fixture system rule'},{'role':'developer','content':'fixture developer rule'},
         {'role':'user','content':'fixture older question'},{'role':'assistant','content':'fixture older answer'},
         {'role':'user','content':'current text'}]
def call(cid):return {'id':cid,'type':'function','function':{'name':'fixture_echo','arguments':'{"x":"value"}'}}

class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'.test-tmp';folder.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=folder)
        self.root=Path(self.tmp.name);self.store=SendModeSettings(self.root/'send-mode.json')
    def tearDown(self):self.tmp.cleanup()
    def test_default_normal_without_writing(self):
        self.assertEqual(self.store.get_mode(),'normal');self.assertFalse(self.store.path.exists())
    def test_saved_modes_reload(self):
        self.store.save('full');self.assertEqual(SendModeSettings(self.store.path).get_mode(),'full')
        self.store.save('normal');self.assertEqual(self.store.get_mode(),'normal')
    def test_unknown_modes_rejected(self):
        for mode in ('',None,{},'automatic'):
            with self.assertRaises(ValueError):self.store.save(mode)
        self.assertFalse(self.store.path.exists())
    def test_corrupt_data_not_silently_defaulted(self):
        self.store.path.write_text('bad fixture json')
        with self.assertRaises(ValueError):self.store.get_mode()
        self.assertTrue(self.store.status()['error']);self.assertIsNone(self.store.status()['mode'])
        self.store.save('normal');self.assertEqual(self.store.get_mode(),'normal')
    def test_atomic_failure_preserves_previous_value(self):
        self.store.save('full')
        with patch('desktop.send_settings.os.replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.store.save('normal')
        self.assertEqual(self.store.get_mode(),'full');self.assertEqual(list(self.root.glob('*.tmp')),[])
    def test_desktop_save_does_not_touch_gateway_or_bridges(self):
        c=ConsoleCore(self.root);c.proc=Mock();c.proc.poll.return_value=None;c.start=Mock();c._terminate=Mock()
        c.saved_bridges=[{'name':'fixture','cdp_port':36938}];c._http=Mock(side_effect=AssertionError('no HTTP needed'))
        c.save_send_mode({'mode':'full','confirmed':True})
        self.assertEqual(c.boot()['send_settings']['mode'],'full');c.start.assert_not_called();c._terminate.assert_not_called()
        self.assertFalse(c.config_file.exists());self.assertEqual(len(c.saved_bridges),1)
    def test_desktop_requires_explicit_save(self):
        c=ConsoleCore(self.root)
        with self.assertRaises(ValueError):c.save_send_mode({'mode':'full'})
        self.assertFalse(c.send_settings.path.exists())
    def test_old_running_gateway_is_not_silently_accepted(self):
        c=ConsoleCore(self.root);c.start=Mock();c.status=Mock(return_value={'compatible':True,'version':'0.5.0'})
        with self.assertRaisesRegex(ValueError,'不支持发送模式'):c.workspace_start()

class PromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY,'title':'fixture'})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
        self.b.execute=AsyncMock(return_value={'ok':True,'completed':True,'text':'fixture response'})
    async def send(self,messages=None,**extra):
        return await self.b.completion({'model':'arena-bound','messages':messages or HISTORY,**extra})
    def prompt(self):return self.b.execute.call_args.kwargs['text']
    async def test_normal_is_plain_current_user_text(self):
        await self.send();self.assertEqual(self.prompt(),'current text')
    async def test_normal_ignores_old_huge_history_not_current_input(self):
        await self.send([{'role':'system','content':'x'*220000},{'role':'user','content':'current'}])
        self.assertEqual(self.prompt(),'current')
        with self.assertRaises(ProtocolError):await self.send([{'role':'user','content':'x'*200001}])
    async def test_full_preserves_previous_rendering(self):
        self.b.send_mode_provider=lambda:'full';await self.send()
        self.assertEqual(self.prompt(),'\n\n'.join(m['role']+': '+m['content'] for m in HISTORY))
    async def test_text_parts_normalized_without_role_prefix(self):
        await self.send([{'role':'user','content':[{'type':'text','text':'one'},{'type':'text','text':'two'}]}])
        self.assertEqual(self.prompt(),'one\ntwo')
    async def test_current_tool_schema_kept_but_old_messages_omitted(self):
        await self.send(tools=[TOOL]);p=self.prompt();self.assertIn('fixture_echo',p);self.assertIn('current text',p)
        self.assertNotIn('fixture older',p);self.assertNotIn('fixture system rule',p);self.assertNotIn('fixture developer rule',p)
    async def test_only_latest_complete_tool_batch_sent(self):
        messages=HISTORY+[
            {'role':'assistant','content':None,'tool_calls':[call('old-call')]},
            {'role':'tool','tool_call_id':'old-call','content':'old observation'},
            {'role':'assistant','content':'do not replay this prose','tool_calls':[call('new-one'),call('new-two')]},
            {'role':'tool','tool_call_id':'new-one','content':'current observation one'},
            {'role':'tool','tool_call_id':'new-two','content':'current observation two'}]
        await self.send(messages,tools=[TOOL]);p=self.prompt()
        for value in ['new-one','new-two','current observation one','current observation two']:self.assertIn(value,p)
        for value in ['old-call','old observation','current text','fixture older','do not replay this prose']:self.assertNotIn(value,p)
    async def test_incomplete_tool_batch_still_rejected_before_send(self):
        with self.assertRaises(ProtocolError):await self.send(HISTORY+[{'role':'assistant','tool_calls':[call('unfinished')],'content':None}],tools=[TOOL])
        self.b.execute.assert_not_awaited()
    async def test_ambiguous_trailing_roles_never_replay_old_user(self):
        for role in ('assistant','system','developer'):
            with self.assertRaises(ProtocolError):await self.send(HISTORY+[{'role':role,'content':'not a new user message'}])
        self.b.execute.assert_not_awaited()
    async def test_request_field_cannot_override_gateway_mode(self):
        await self.send(send_mode='full');self.assertEqual(self.prompt(),'current text')
    async def test_mode_provider_failure_blocks_send(self):
        self.b.send_mode_provider=Mock(side_effect=ValueError('fixture private config'))
        with self.assertRaises(HTTPException) as ctx:await self.send()
        self.assertEqual(ctx.exception.status_code,503);self.assertNotIn('fixture private',ctx.exception.detail)
        self.b.execute.assert_not_awaited()
    async def test_stream_keeps_request_entry_mode(self):
        mode=['normal'];self.b.send_mode_provider=lambda:mode[0]
        response=await self.send(stream=True);mode[0]='full'
        frames=''.join([chunk async for chunk in response.body_iterator])
        self.assertIn('[DONE]',frames);self.assertEqual(self.prompt(),'current text')
        await self.send();self.assertIn('fixture older question',self.prompt())
    async def test_queued_requests_keep_their_snapshot(self):
        mode=['normal'];self.b.send_mode_provider=lambda:mode[0]
        gate=asyncio.Event();entered=asyncio.Event();sent=[]
        async def execute(kind,**kwargs):
            sent.append(kwargs['text']);entered.set();await gate.wait();return {'ok':True,'completed':True,'text':'fixture'}
        self.b.execute=execute
        first=asyncio.create_task(self.send());await entered.wait()
        second=asyncio.create_task(self.send())
        for _ in range(10):await asyncio.sleep(0)
        mode[0]='full';gate.set();await asyncio.gather(first,second)
        self.assertEqual(sent,['current text','current text'])
    async def test_old_tools_do_not_turn_plain_user_into_replayed_tool_context(self):
        messages=[{'role':'assistant','content':None,'tool_calls':[call('old')]},{'role':'tool','tool_call_id':'old','content':'old observation'},{'role':'user','content':'hello now'}]
        await self.send(messages);self.assertEqual(self.prompt(),'hello now')

    async def test_saved_file_changes_apply_to_next_request_without_rebind(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as d:
            store=SendModeSettings(Path(d)/'send-mode.json');self.b.send_mode_provider=store.get_mode
            await self.send();self.assertEqual(self.prompt(),'current text')
            store.save('full');await self.send();self.assertIn('fixture system rule',self.prompt())
            store.save('normal');await self.send();self.assertEqual(self.prompt(),'current text')
            self.assertTrue(self.b.binding['allow_clients'])

    async def test_health_reports_running_gateway_mode_and_config_error(self):
        import server
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as d:
            store=SendModeSettings(Path(d)/'send-mode.json')
            with patch.object(server,'send_mode_settings',store):
                h=await server.health();self.assertTrue(h['send_mode_supported']);self.assertEqual(h['send_mode'],'normal')
                store.save('full');self.assertEqual((await server.health())['send_mode'],'full')
                store.path.write_text('bad fixture json');h=await server.health();self.assertTrue(h['send_mode_error']);self.assertIsNone(h['send_mode'])

if __name__=='__main__':unittest.main()
