"""Offline names-only audit of the actual selected definition block."""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from tool_audit import begin_audit, finish_audit
from tool_prompt import format_tools, format_tool_table
from openai_compat import normalize_request, render_agent_prompt, ProtocolError
from conversation_bridge import ConversationBridge
from test_tool_compaction import fixtures
from test_conversation_queue import title_body

class AuditTests(unittest.TestCase):
    def test_all_formats_read_actual_names_only(self):
        tools=fixtures(3)
        for definitions in (json.dumps(tools),format_tools(tools,16,0),format_tool_table(tools),format_tool_table(tools,deduplicate=True)):
            a=begin_audit(tools,True);finish_audit(a,definitions)
            self.assertTrue(a['names_match']);self.assertEqual(a['rendered_count'],3)
            self.assertEqual(a['received_names'],a['rendered_names'])
    def test_missing_name_not_hidden_by_name_inside_schema(self):
        tools=fixtures(2);subset=copy.deepcopy(tools[:1]);subset[0]['function']['parameters']['const']='fixture_1'
        a=begin_audit(tools,True);finish_audit(a,json.dumps(subset))
        self.assertFalse(a['names_match']);self.assertEqual(a['missing_names'],['fixture_1'])
    def test_extra_and_duplicate_detected(self):
        tools=fixtures(2);a=begin_audit(tools,True);finish_audit(a,json.dumps([tools[0],tools[0]]))
        self.assertEqual(a['missing_names'],['fixture_1']);self.assertEqual(a['unexpected_names'],['fixture_0'])
    def test_malformed_table_index_unverified(self):
        a=begin_audit(fixtures(1),True)
        finish_audit(a,'Tool table: zero-based index into schemas\n'+json.dumps({'schemas':[], 'tools':[['fixture_0',3]]}))
        self.assertIsNone(a['names_match']);self.assertEqual(a['format'],'unverified')
    def test_none_policy_explicitly_excludes_tools(self):
        b,p,_=normalize_request({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],'tools':fixtures(2),'tool_choice':'none'})
        a=begin_audit(p.tools,p.active);render_agent_prompt(b['messages'],p,200000,audit=a)
        self.assertEqual(a['rendered_count'],0);self.assertTrue(a['names_match']);self.assertEqual(len(a['excluded_names']),2)
    def test_228_tools_selected_compact_variant(self):
        b,p,_=normalize_request({'model':'arena-bound','messages':[{'role':'user','content':'PRIVATE_HISTORY_MARKER'}],'tools':fixtures()})
        a=begin_audit(p.tools,p.active);text=render_agent_prompt(b['messages'],p,200000,utf16=True,audit=a)
        self.assertEqual(a['format'],'compact_list');self.assertEqual(a['rendered_count'],228);self.assertTrue(a['names_match'])
        for marker in ('PRIVATE_HISTORY_MARKER','Parameter guidance','Long descriptive fixture','parameters','pattern'):
            self.assertNotIn(marker,json.dumps(a))
    def test_shared_variant_and_prompt_unchanged(self):
        b,p,_=normalize_request({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],'tools':fixtures()})
        a=begin_audit(p.tools,p.active);text=render_agent_prompt(b['messages'],p,15000,audit=a)
        self.assertEqual(a['format'],'shared_schema_table');self.assertEqual(a['rendered_count'],228)
        self.assertEqual(text,render_agent_prompt(b['messages'],p,15000))
    def test_budget_failure_not_claimed_dispatched(self):
        tools=fixtures(1);tools[0]['function']['parameters']['const']='x'*210000
        b,p,_=normalize_request({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],'tools':tools})
        a=begin_audit(p.tools,p.active)
        with self.assertRaises(ProtocolError):render_agent_prompt(b['messages'],p,200000,audit=a)
        self.assertEqual(a['stage'],'budget_rejected');self.assertTrue(a['names_match'])

class BridgeAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=ConversationBridge();client='d'*32;key='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
        self.b.offer({'client_id':client,'context_key':key});self.b.bind({'client_id':client,'context_key':key,'confirmed':True,'allow_clients':True})
        self.b.execute=AsyncMock(return_value={'ok':True,'text':'OK'})
    async def test_status_snapshot_no_body_and_title_does_not_overwrite(self):
        await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'PRIVATE_HISTORY_MARKER'}],'tools':fixtures(2)})
        a=self.b.status()['tool_audit'];self.assertEqual(a['received_count'],2)
        await self.b.completion(title_body());self.assertEqual(self.b.status()['tool_audit'],a)
        await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'no tools'}]});self.assertEqual(self.b.status()['tool_audit'],a)
        a['received_names'].clear();self.assertEqual(len(self.b.status()['tool_audit']['received_names']),2)
        self.assertNotIn('PRIVATE_HISTORY_MARKER',json.dumps(self.b.status()))
    async def test_new_tool_request_replaces_old_and_restart_empty(self):
        for i in (1,2):
            await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],'tools':fixtures(i)})
            self.assertEqual(self.b.status()['tool_audit']['sequence'],i)
            self.assertEqual(self.b.status()['tool_audit']['received_count'],i)
        self.assertIsNone(ConversationBridge().status()['tool_audit'])
    async def test_budget_rejection_still_available_without_send(self):
        with self.assertRaises(ProtocolError):
            await self.b.completion({'model':'arena-bound','messages':[{'role':'user','content':'x'*210000}],'tools':fixtures(1)})
        self.b.execute.assert_not_awaited();self.assertEqual(self.b.status()['tool_audit']['stage'],'budget_rejected')

if __name__=='__main__':unittest.main(verbosity=2)
