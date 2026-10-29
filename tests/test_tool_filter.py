"""Per-model tool allowlist: fixture files and mocked sends only; no live gateway or account."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from conversation_bridge import ConversationBridge
from openai_compat import ProtocolError
from tool_filter import ToolFilter, DEFAULT_TOOLS

CLIENT = 'a' * 32
KEY = 'https://arena.ai/agent/11111111-1111-4111-8111-111111111111'


def tool(name):
    return {'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object', 'properties': {}}}}


NAMES = ['read', 'write', 'edit', 'pwsh', 'mcp__amap__maps_weather', 'mcp__rea__open_binary', 'spawn_teammate']


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'tool-filter.json'
        self.f = ToolFilter(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def names(self, alias):
        kept, removed = self.f.apply(alias, [tool(n) for n in NAMES])
        return [t['function']['name'] for t in kept], removed

    def test_missing_file_keeps_default_dev_tools_in_client_order(self):
        kept, removed = self.names('opus5.5')
        self.assertEqual(kept, ['read', 'write', 'edit', 'pwsh'])
        self.assertEqual(removed, ['mcp__amap__maps_weather', 'mcp__rea__open_binary', 'spawn_teammate'])
        self.assertFalse(self.path.exists())

    def test_per_model_rule_star_and_patterns(self):
        self.f.save({'version': 1, 'models': {'all': '*', 're': ['read', 'mcp__rea__*']}})
        self.assertEqual(self.names('all')[1], [])
        self.assertEqual(self.names('re')[0], ['read', 'mcp__rea__open_binary'])
        self.assertEqual(self.names('other')[0], ['read', 'write', 'edit', 'pwsh'])

    def test_custom_default(self):
        self.f.save({'version': 1, 'default': ['pwsh'], 'models': {}})
        self.assertEqual(self.names('x')[0], ['pwsh'])

    def test_malformed_file_fails_closed(self):
        for raw in ('not json', '[]', '{"version":2}', '{"version":1,"models":{"a":"all"}}', '{"version":1,"default":[1]}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.f.apply('a', [tool('read')])
                self.assertTrue(self.f.status()['error'])

    def test_save_rejects_invalid_and_keeps_old_file(self):
        self.f.save({'version': 1, 'default': ['read'], 'models': {}})
        with self.assertRaises(ValueError):
            self.f.save({'version': 1, 'default': 'everything'})
        self.assertEqual(self.f.load()['default'], ['read'])
        self.assertEqual(list(Path(self.dir.name).glob('*.tmp')), [])

    def test_default_set_is_dev_basics(self):
        for name in ('read', 'write', 'edit', 'glob', 'grep', 'pwsh', 'todo_write', 'ask_user_question'):
            self.assertIn(name, DEFAULT_TOOLS)


class BridgeFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.f = ToolFilter(Path(self.dir.name) / 'tool-filter.json')
        b = ConversationBridge()
        b.offer({'client_id': CLIENT, 'context_key': KEY, 'title': 'fixture'})
        b.bind({'client_id': CLIENT, 'context_key': KEY, 'confirmed': True, 'allow_clients': True})
        b.execute = AsyncMock(return_value={'ok': True, 'text': 'ok'})
        b.tool_filter_provider = lambda tools: self.f.apply('opus5.5', tools)
        self.b = b

    def tearDown(self):
        self.dir.cleanup()

    def body(self, **extra):
        return {'model': 'arena-bound', 'tools': [tool(n) for n in NAMES],
                'messages': [{'role': 'user', 'content': 'hi'}], **extra}

    async def test_prompt_only_contains_allowed_tools_and_audit_records_removed(self):
        await self.b.completion(self.body())
        prompt = self.b.execute.call_args.kwargs['text']
        for name in ('read', 'write', 'edit', 'pwsh'):
            self.assertIn(name, prompt)
        for name in ('mcp__amap__maps_weather', 'mcp__rea__open_binary', 'spawn_teammate'):
            self.assertNotIn(name, prompt)
        audit = self.b.status()['tool_audit']
        self.assertEqual(audit['client_count'], 7)
        self.assertEqual(audit['received_count'], 4)
        self.assertEqual(audit['filtered_names'], ['mcp__amap__maps_weather', 'mcp__rea__open_binary', 'spawn_teammate'])
        self.assertTrue(audit['names_match'])

    async def test_model_call_to_filtered_tool_is_not_returned_as_tool_call(self):
        self.b.execute = AsyncMock(return_value={'ok': True, 'text': '<tool_call>{"name":"mcp__rea__open_binary","arguments":{}}</tool_call>'})
        try:
            result = await self.b.completion(self.body())
        except (ProtocolError, HTTPException):
            return
        calls = result['choices'][0]['message'].get('tool_calls') or []
        self.assertFalse(any(c['function']['name'] == 'mcp__rea__open_binary' for c in calls))

    async def test_required_with_nothing_allowed_is_rejected_before_send(self):
        self.f.save({'version': 1, 'default': [], 'models': {}})
        with self.assertRaises(ProtocolError):
            await self.b.completion(self.body(tool_choice='required'))
        self.b.execute.assert_not_called()

    async def test_malformed_config_blocks_send(self):
        self.f.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(HTTPException) as ctx:
            await self.b.completion(self.body())
        self.assertEqual(ctx.exception.status_code, 503)
        self.b.execute.assert_not_called()

    async def test_bridge_without_provider_keeps_everything(self):
        del self.b.tool_filter_provider
        await self.b.completion(self.body())
        self.assertIn('mcp__rea__open_binary', self.b.execute.call_args.kwargs['text'])


if __name__ == '__main__':
    unittest.main()
