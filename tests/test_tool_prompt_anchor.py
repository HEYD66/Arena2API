"""Offline: closing format reminder and tool-result status notes in the rendered prompt."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openai_compat import (normalize_request, render_agent_prompt, annotate_tool_results,
                           closing_reminder, REMINDER_HEADER, parse_calls)
from tool_reference import render_reference_prompt

TOOL = {'type': 'function', 'function': {'name': 'pwsh', 'description': 'run', 'parameters': {
    'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}}
HISTORY = 'Conversation in chronological order (tool results are client observations):\n'


def request(messages, **kw):
    return normalize_request({'model': 'arena-bound', 'tools': [TOOL], 'messages': messages, **kw})


def tool_turn(result, name='pwsh'):
    return [{'role': 'user', 'content': 'do it'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1', 'type': 'function',
             'function': {'name': name, 'arguments': '{"command":"x"}'}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'content': result}]


def history_of(text):
    return json.loads(text.split(HISTORY, 1)[1].split('\n\n' + REMINDER_HEADER, 1)[0])


class ReminderTests(unittest.TestCase):
    def test_reminder_is_last_and_after_history(self):
        b, p, _ = request([{'role': 'user', 'content': 'hi'}])
        text = render_agent_prompt(b['messages'], p, 200000)
        self.assertGreater(text.index(REMINDER_HEADER), text.index(HISTORY))
        self.assertTrue(text.endswith('</tool_call>'))
        self.assertIn('NOT executed', text)
        self.assertIn('Never claim you have no tools', text)

    def test_reminder_example_is_never_executable_alone(self):
        # 示例用占位工具名：即使模型原样抄回来，也会因为工具未声明而被拒，不会被执行
        b, p, _ = request([{'role': 'user', 'content': 'hi'}])
        example = closing_reminder(p).rsplit('\n', 1)[1]
        with self.assertRaises(Exception):
            parse_calls(example, p)

    def test_reminder_respects_mode(self):
        b, p, _ = request([{'role': 'user', 'content': 'hi'}], tool_choice='required')
        self.assertIn('MUST contain a tool call', closing_reminder(p))
        b, p, _ = request([{'role': 'user', 'content': 'hi'}], parallel_tool_calls=False)
        self.assertIn('at most ONE tool call', closing_reminder(p))
        b, p, _ = request([{'role': 'user', 'content': 'hi'}], tool_choice={'type': 'function', 'function': {'name': 'pwsh'}})
        self.assertIn('Call only the tool pwsh', closing_reminder(p))

    def test_no_reminder_without_active_tools(self):
        b, p, _ = request([{'role': 'user', 'content': 'hi'}], tool_choice='none')
        self.assertNotIn(REMINDER_HEADER, render_agent_prompt(b['messages'], p, 200000))

    def test_reminder_dropped_before_budget_rejection(self):
        b, p, _ = request([{'role': 'user', 'content': 'hi'}])
        full = render_agent_prompt(b['messages'], p, 200000)
        bare = full.split('\n\n' + REMINDER_HEADER, 1)[0]
        text = render_agent_prompt(b['messages'], p, len(bare))
        self.assertEqual(text, bare)

    def test_reference_renderer_keeps_its_native_allowance(self):
        b, p, _ = request([{'role': 'user', 'content': 'hi'}])
        text = render_reference_prompt(b['messages'], p, 200000)
        self.assertTrue(text.rstrip().endswith('</tool_call>'))
        self.assertIn('except the exact read-only reference lookup', text)


class ResultNoteTests(unittest.TestCase):
    def note(self, result, name='pwsh'):
        b, p, _ = request(tool_turn(result, name))
        content = history_of(render_agent_prompt(b['messages'], p, 200000))[2]['content']
        self.assertTrue(content.endswith(result), content)  # 原文完整保留
        return content[:-len(result)]

    def test_success_failure_empty_interrupted(self):
        self.assertIn('succeeded (exit 0)', self.note('done\n'))
        self.assertIn('FAILED with exit code 2', self.note('boom\n[exit code: 2]'))
        self.assertIn('no output', self.note('(no output)'))
        self.assertIn('interrupted', self.note('partial\n[timed out after 120000ms]'))

    def test_non_shell_tools_only_get_empty_note(self):
        msgs = annotate_tool_results(tool_turn('file text', name='read'))
        self.assertEqual(msgs[2]['content'], 'file text')
        msgs = annotate_tool_results(tool_turn('', name='write'))
        self.assertIn('no output', msgs[2]['content'])

    def test_request_messages_not_mutated(self):
        msgs = tool_turn('ok')
        b, p, _ = request(msgs)
        before = json.dumps(b['messages'])
        render_agent_prompt(b['messages'], p, 200000)
        self.assertEqual(json.dumps(b['messages']), before)


if __name__ == '__main__':
    unittest.main()
