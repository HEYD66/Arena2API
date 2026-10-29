"""回传工具结果时省略大段工具参数（离线，无网络）。"""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from send_content import select_current_messages, ARG_KEEP_CHARS


def batch(args):
    return [{'role': 'user', 'content': 'go'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1', 'type': 'function',
             'function': {'name': 'pwsh', 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'pwsh', 'content': 'ok'}]


class CompactionTests(unittest.TestCase):
    def test_large_argument_replaced_small_kept(self):
        body = '<html><script>' + 'x' * (ARG_KEEP_CHARS + 10)
        out = select_current_messages(batch({'description': 'Write file', 'command': body}))
        call = out[0]['tool_calls'][0]
        args = json.loads(call['function']['arguments'])
        self.assertEqual(args['description'], 'Write file')
        self.assertNotIn('<script>', args['command'])
        self.assertIn('已省略', args['command'])
        self.assertEqual((call['id'], call['function']['name']), ('c1', 'pwsh'))
        self.assertEqual(out[1]['content'], 'ok')

    def test_short_arguments_unchanged(self):
        args = json.dumps({'command': 'Get-Location'})
        out = select_current_messages(batch({'command': 'Get-Location'}))
        self.assertEqual(out[0]['tool_calls'][0]['function']['arguments'], args)


if __name__ == '__main__':
    unittest.main()
