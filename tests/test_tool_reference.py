"""Offline tests: no account/network requests; no production configuration reads."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from conversation_bridge import ConversationBridge
from openai_compat import normalize_request, parse_calls, ProtocolError
from source_router import SourceRouter
from tool_reference import fingerprint, render_reference_prompt, manifest, trial_eligible
from fastapi import HTTPException

TOOL={'type':'function','function':{'name':'fixture_read','description':'READONLY FIXTURE DESCRIPTION','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}}
KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
CLIENT='a'*32;ACCOUNT='b'*64
ROW={'alias':'opus5.5','client_id':CLIENT,'account_key':ACCOUNT,'context_key':KEY,'source_label':'实例5','title':'fixture','account_label':'fixture','allow_clients':True,'enabled':False}
def metadata():return {'tools':{'fixture_read':fingerprint(TOOL)},'index_sha256':'a'*64,'reference_sha256':'b'*64}
def request(**extra):return dict({'model':'opus5.5','messages':[{'role':'user','content':'fixture question'}],'tools':[copy.deepcopy(TOOL)]},**extra)

class ReferencePromptTests(unittest.TestCase):
    def render(self,body=None,budget=200000,audit=None):
        body,policy,_=normalize_request(body or request())
        with patch('tool_reference.manifest',return_value=metadata()):return render_reference_prompt(body['messages'],policy,budget,audit=audit),policy
    def test_exact_definitions_are_replaced_by_paths_and_names(self):
        audit={};text,policy=self.render(audit=audit)
        self.assertIn('/home/user/uploads/tools-index.md',text);self.assertIn('/home/user/uploads/tools-reference.md',text)
        self.assertNotIn('READONLY FIXTURE DESCRIPTION',text);self.assertIn('fixture_read',text)
        self.assertEqual(audit['reference_count'],1);self.assertEqual(audit['inline_definition_count'],0)
        self.assertTrue(audit['names_match']);self.assertEqual(policy.tools,[TOOL])
    def test_changed_definition_inlined(self):
        b=request();b['tools'][0]['function']['description']='UPDATED FIXTURE'
        audit={};text,_=self.render(b,audit=audit)
        self.assertIn('UPDATED FIXTURE',text);self.assertEqual(audit['reference_count'],0);self.assertEqual(audit['inline_definition_count'],1)
    def test_unknown_definition_inlined(self):
        b=request();b['tools'][0]['function']['name']='new_fixture'
        text,_=self.render(b);self.assertIn('READONLY FIXTURE DESCRIPTION',text)
    def test_original_allowlist_still_rejects_undeclared_tools(self):
        _,policy=self.render()
        with self.assertRaisesRegex(ProtocolError,'undeclared'):parse_calls('<tool_call>{"name":"other_tool","arguments":{}}</tool_call>',policy)
        self.assertEqual(parse_calls('<tool_call>{"name":"fixture_read","arguments":{"path":"fixture"}}</tool_call>',policy)[0]['function']['name'],'fixture_read')
    def test_required_selected_and_parallel_policies_retained(self):
        for choice in ['required',{'type':'function','function':{'name':'fixture_read'}}]:
            text,policy=self.render(request(tool_choice=choice,parallel_tool_calls=False))
            self.assertIn('Return at most ONE',text)
            with self.assertRaises(ProtocolError):parse_calls('ordinary text',policy)
    def test_budget_is_enforced_without_sending(self):
        with self.assertRaises(ProtocolError):self.render(budget=10)
    def test_manifest_has_expected_snapshot_count(self):
        data=manifest();self.assertEqual(len(data['tools']),0);self.assertEqual(len(data['reference_sha256']),64)
    def test_fingerprints_ignore_key_order_not_content(self):
        self.assertEqual(fingerprint(TOOL),fingerprint(dict(reversed(list(TOOL.items())))))
        t=copy.deepcopy(TOOL);t['function']['parameters']['required']=[]
        self.assertNotEqual(fingerprint(TOOL),fingerprint(t))

class ScopedReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'routes.json';self.r=SourceRouter(self.path);self.r.routes['opus5.5']=copy.deepcopy(ROW)
    def tearDown(self):self.tmp.cleanup()
    def pin(self):return CLIENT,ACCOUNT,KEY
    async def enable(self):return await self.r.control({'alias':'opus5.5','action':'reference_on','confirmed':True})
    async def test_default_off_and_explicit_enable_disable_persist(self):
        self.assertFalse(self.r.reference_enabled('opus5.5',self.pin()));self.assertFalse(self.path.exists())
        await self.enable();self.assertTrue(self.r.reference_enabled('opus5.5',self.pin()))
        loaded=SourceRouter(self.path);self.assertTrue(loaded.reference_enabled('opus5.5',self.pin()));self.assertFalse(loaded.routes['opus5.5']['enabled'])
        await self.r.control({'alias':'opus5.5','action':'reference_off','confirmed':True});self.assertFalse(self.r.reference_enabled('opus5.5',self.pin()))
    async def test_scope_and_confirmation(self):
        with self.assertRaises(HTTPException):await self.r.control({'alias':'opus5.5','action':'reference_on'})
        self.r.routes['opus5.5']['source_label']='实例6'
        with self.assertRaises(HTTPException):await self.enable()
        self.assertFalse(self.path.exists())
        self.assertFalse(trial_eligible(dict(ROW,alias='other')))
    async def test_pin_does_not_transfer_to_other_source_account_or_conversation(self):
        await self.enable()
        for pin in [('c'*32,ACCOUNT,KEY),(CLIENT,'c'*64,KEY),(CLIENT,ACCOUNT,KEY+'x')]:self.assertFalse(self.r.reference_enabled('opus5.5',pin))
    async def test_failed_save_rolls_back(self):
        with patch.object(self.r,'save',side_effect=OSError('fixture')):
            with self.assertRaises(HTTPException):await self.enable()
        self.assertNotIn('tool_reference',self.r.routes['opus5.5'])
    async def test_uncertainty_blocks_setting_changes(self):
        self.r.routes['opus5.5']['pending_review']=True
        with self.assertRaises(HTTPException):await self.enable()
    async def test_delete_and_recreate_does_not_inherit(self):
        await self.enable();await self.r.control({'alias':'opus5.5','action':'delete','confirmed':True})
        self.r.routes['opus5.5']=copy.deepcopy(ROW)
        self.assertFalse(self.r.reference_enabled('opus5.5',self.pin()))
    async def test_corrupt_marker_fails_closed(self):
        self.path.write_text(json.dumps([dict(ROW,tool_reference='true')]))
        loaded=SourceRouter(self.path);self.assertTrue(loaded.load_error)

class ReferenceCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();self.b.offer({'client_id':CLIENT,'context_key':KEY,'title':'fixture'})
        self.b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
        self.b.queued_send=AsyncMock(return_value={'ok':True,'completed':True,'text':'fixture response'})
    async def test_default_legacy_and_opt_in_prompt(self):
        await self.b.completion(request());self.assertIn('READONLY FIXTURE DESCRIPTION',self.b.queued_send.call_args.args[0])
        self.b.tool_reference_provider=lambda:True
        with patch('tool_reference.manifest',return_value=metadata()):await self.b.completion(request())
        self.assertIn('CLIENT TOOL REFERENCE TRIAL',self.b.queued_send.call_args.args[0]);self.assertNotIn('READONLY FIXTURE DESCRIPTION',self.b.queued_send.call_args.args[0])
    async def test_none_and_plain_requests_do_not_use_reference(self):
        self.b.tool_reference_provider=lambda:True
        for b in [request(tool_choice='none'),request(tools=[])]:
            with patch('conversation_bridge.render_reference_prompt',side_effect=AssertionError('must not render')):await self.b.completion(b)
    async def test_full_and_normal_modes_preserve_message_selection(self):
        self.b.tool_reference_provider=lambda:True;b=request();b['messages'].insert(0,{'role':'system','content':'old fixture system'})
        for mode in ['normal','full']:
            self.b.send_mode_provider=lambda:mode
            with patch('tool_reference.manifest',return_value=metadata()):await self.b.completion(b)
            self.assertEqual('old fixture system' in self.b.queued_send.call_args.args[0],mode=='full')
    async def test_reference_error_blocks_submission(self):
        self.b.tool_reference_provider=lambda:True
        with patch('tool_reference.manifest',side_effect=ProtocolError('fixture')):
            with self.assertRaises(ProtocolError):await self.b.completion(request())
        self.b.queued_send.assert_not_called()

if __name__=='__main__':unittest.main()
