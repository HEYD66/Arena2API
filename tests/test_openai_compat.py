"""Offline Chat Completions contract and two-turn tool-loop tests.
No listening server, browser, account, or external network. unittest + existing deps.
"""
import asyncio
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ARENA_LOG_TO_FILE"] = "0"
os.environ["ARENA_TRACE"] = "0"
os.environ["ARENA_CAPTURE_UI"] = "0"
os.environ["ARENA_RENAME_CHATS"] = "0"
os.environ["ARENA_SETTINGS_FILE"] = str(ROOT / "tests" / "nonexistent-settings.json")
from openai_compat import normalize_request, parse_calls, ProtocolError, render_agent_prompt
import httpx
import server
import tool_prompt


TOOL = {"type": "function", "function": {"name": "read_file", "description": "Read a test fixture", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
CALL_TEXT = '<tool_call>{"name":"read_file","arguments":{"path":"example.txt"}}</tool_call>'


def request(**kw):
    r = {"model": "fixture-model", "messages": [{"role": "user", "content": "Read the fixture"}], "tools": [copy.deepcopy(TOOL)]}
    r.update(kw)
    return r


class ContractTests(unittest.TestCase):
    def test_auto_calls(self):
        _, policy, agent = normalize_request(request())
        self.assertTrue(agent)
        calls = parse_calls(CALL_TEXT, policy)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "example.txt"})
        self.assertTrue(calls[0]["id"].startswith("call_"))

    def test_none(self):
        b, p, _ = normalize_request(request(tool_choice="none"))
        self.assertEqual(b["tools"], [])
        self.assertIsNone(parse_calls(CALL_TEXT, p))

    def test_required_and_selected(self):
        for choice in ("required", {"type": "function", "function": {"name": "read_file"}}):
            _, p, _ = normalize_request(request(tool_choice=choice))
            self.assertEqual(len(parse_calls(CALL_TEXT, p)), 1)
            with self.assertRaises(ProtocolError):
                parse_calls("ordinary text", p)

    def test_no_code_fence_guess(self):
        _, p, _ = normalize_request(request())
        self.assertIsNone(parse_calls('```json\n{"name":"read_file","arguments":{}}\n```', p))

    def test_quoted_literal_tag_is_normal_text(self):
        _,p,_=normalize_request(request())
        for text in ('讲解：`<tool_call>` 是一个标签。', '示例 ``<tool_call>``，不是执行请求。', '```<tool_call>```', '闭合标签 `</tool_call>` 也只是文本。'):
            with self.subTest(text=text):self.assertIsNone(parse_calls(text,p))
    def test_quoted_complete_call_is_not_executed(self):
        _,p,_=normalize_request(request())
        self.assertIsNone(parse_calls('`'+CALL_TEXT+'`',p))
    def test_fenced_calls_are_examples(self):
        _,p,_=normalize_request(request())
        for text in ('```xml\n'+CALL_TEXT+'\n```','~~~xml\n'+CALL_TEXT+'\n~~~','```xml\n<tool_call>'):
            with self.subTest(text=text):self.assertIsNone(parse_calls(text,p))
    def test_example_then_real_call(self):
        _,p,_=normalize_request(request())
        calls=parse_calls('示例 `<tool_call>`。\n'+CALL_TEXT,p)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['function']['name'],'read_file')
    def test_markers_and_backticks_inside_arguments_unchanged(self):
        _,p,_=normalize_request(request())
        args={'path':'literal `code` <tool_call> </tool_call>'}
        text='<tool_call>'+json.dumps({'name':'read_file','arguments':args})+'</tool_call>'
        calls=parse_calls(text,p);self.assertEqual(json.loads(calls[0]['function']['arguments']),args)
    def test_real_unclosed_call_still_rejected(self):
        _,p,_=normalize_request(request())
        for text in ('先解释 `<tool_call>`。\n<tool_call>{"name":"read_file","arguments":{}}','<tool_call>{"name":"read_file","arguments":','</tool_call>'):
            with self.subTest(text=text),self.assertRaises(ProtocolError):parse_calls(text,p)
    def test_required_call_not_satisfied_by_example(self):
        _,p,_=normalize_request(request(tool_choice='required'))
        with self.assertRaises(ProtocolError):parse_calls('`'+CALL_TEXT+'`',p)

    def test_bad_model_outputs(self):
        _, p, _ = normalize_request(request())
        for text in ('<tool_call>{}', '<tool_call>bad</tool_call>', '<tool_call>{"name":"unknown","arguments":{}}</tool_call>', '<tool_call>{"name":"read_file","arguments":"bad"}</tool_call>'):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                parse_calls(text, p)

    def test_incomplete_or_length_tool_output(self):
        _, p, _ = normalize_request(request())
        for reason in ("length", "incomplete", "content_filter"):
            with self.subTest(reason=reason), self.assertRaises(ProtocolError):
                parse_calls(CALL_TEXT, p, reason)

    def test_parallel_false(self):
        _, p, _ = normalize_request(request(parallel_tool_calls=False))
        with self.assertRaises(ProtocolError):
            parse_calls(CALL_TEXT * 2, p)

    def test_parallel_distinct_ids(self):
        _, p, _ = normalize_request(request())
        calls = parse_calls(CALL_TEXT * 2, p)
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])

    def test_invalid_requests(self):
        for overrides in ({"model": ""}, {"stream": "true"}, {"messages": []}, {"tools": {}}, {"tools": [TOOL, TOOL]}, {"tool_choice": "bad"}, {"tool_choice": {"type": "function", "function": {"name": "missing"}}}, {"parallel_tool_calls": 1}, {"n": 2}, {"response_format": None}, {"response_format": {"type": "json_schema"}}, {"functions": [TOOL]}):
            with self.subTest(overrides=overrides), self.assertRaises(ProtocolError):
                normalize_request(request(**overrides))
        with self.assertRaises(ProtocolError):
            normalize_request([])

    def test_text_parts_developer(self):
        b, _, _ = normalize_request(request(messages=[{"role": "developer", "content": "Follow the task"}, {"role": "user", "content": [{"type": "text", "text": "你好"}]}]))
        self.assertEqual(b["messages"][1]["content"], "你好")

    def test_missing_duplicate_or_orphan_results(self):
        _, p, _ = normalize_request(request())
        calls = parse_calls(CALL_TEXT, p)
        assistant = {"role": "assistant", "content": None, "tool_calls": calls}
        result = {"role": "tool", "tool_call_id": calls[0]["id"], "content": "ok"}
        for history in ([assistant], [result], [assistant, result, result], [assistant, {"role": "user", "content": "next"}]):
            with self.subTest(history=history), self.assertRaises(ProtocolError):
                normalize_request(request(messages=history))

    def test_out_of_order_results_retain_names_ids(self):
        _, p, _ = normalize_request(request())
        calls = parse_calls(CALL_TEXT * 2, p)
        history = [{"role": "assistant", "content": None, "tool_calls": calls}] + [{"role": "tool", "tool_call_id": c["id"], "content": "ok"} for c in reversed(calls)]
        b, _, _ = normalize_request(request(messages=history))
        prompt = render_agent_prompt(b["messages"], p, 200000)
        for c in calls:
            self.assertIn(c["id"], prompt)
        self.assertEqual(b["messages"][-1]["name"], "read_file")

    def test_invalid_history_args(self):
        c = {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "invalid"}}
        with self.assertRaises(ProtocolError):
            normalize_request(request(messages=[{"role": "assistant", "tool_calls": [c]}]))

    def test_schema_zero_removes_description(self):
        tools = copy.deepcopy([TOOL])
        tools[0]["function"]["parameters"]["description"] = "SECRET_DESCRIPTION"
        self.assertNotIn("SECRET_DESCRIPTION", tool_prompt.format_tools(tools, param_desc_cap=0))

    def test_request_not_mutated(self):
        r = request()
        original = copy.deepcopy(r)
        normalize_request(r)
        self.assertEqual(r, original)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_alias_and_no_legacy_routes(self):
        with patch.object(server,'verify_api_key'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
                r=await c.get('/v1/models');self.assertEqual([x['id'] for x in r.json()['data']],['arena-bound'])
                r=await c.post('/v1/chat/completions',json=request());self.assertEqual(r.status_code,400)
                self.assertFalse(hasattr(server,'_arena_ctx'))
                self.assertFalse(hasattr(server,'get_v3_token_singleflight'))
    async def test_auth_required(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
            self.assertEqual((await c.get('/v1/models')).status_code,401)
            self.assertEqual((await c.get('/health')).json()['product'],'arena2api-conversations')

if __name__ == '__main__':unittest.main()
