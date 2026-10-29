"""Offline: one automatic retry only when Arena clearly rejected the submission."""
import asyncio
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from conversation_bridge import ConversationBridge

CLIENT = 'd' * 32
KEY = 'https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
REJECTED = {'ok': False, 'completed': False, 'uncertain': False, 'submission_attempted': True,
            'submission_accepted': False, 'upstream_status': 403, 'failure_stage': 3, 'error_code': 'ACCESS_DENIED'}


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b = ConversationBridge();self.b.retry_delay = 0
        self.b.offer({'client_id': CLIENT, 'context_key': KEY})
        self.b.bind({'client_id': CLIENT, 'context_key': KEY, 'confirmed': True, 'allow_clients': True})

    async def run_send(self, *results):
        task = asyncio.create_task(self.b.completion({'model': 'arena-bound', 'messages': [{'role': 'user', 'content': 'hi'}]}))
        commands = []
        for r in results:
            cmd = await asyncio.wait_for(self.b.command(CLIENT), 1);commands.append(cmd)
            self.b.result({'client_id': CLIENT, 'context_key': KEY, 'request_id': cmd['request_id'], 'result': r})
        return task, commands

    async def test_rejected_submit_retried_once_then_succeeds(self):
        task, cmds = await self.run_send(REJECTED, {'ok': True, 'completed': True, 'text': 'OK'})
        r = await task
        self.assertEqual(r['choices'][0]['message']['content'], 'OK')
        self.assertEqual(len(cmds), 2);self.assertEqual(cmds[0]['text'], cmds[1]['text'])
        self.assertNotEqual(cmds[0]['request_id'], cmds[1]['request_id']);self.assertFalse(self.b.uncertain)

    async def test_only_one_retry(self):
        task, _ = await self.run_send(REJECTED, REJECTED)
        with self.assertRaises(HTTPException) as e:await task
        self.assertIn('HTTP403', e.exception.detail)
        with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(self.b.command(CLIENT), 0.2)

    def test_never_retry_when_possibly_accepted_or_uncertain(self):
        ok = ConversationBridge.retry_eligible
        self.assertTrue(ok(REJECTED))
        for change in ({'submission_accepted': True}, {'submission_accepted': None}, {'uncertain': True},
                       {'uncertain': None}, {'submission_attempted': False}, {'failure_stage': 4},
                       {'upstream_status': 401}, {'upstream_status': 500}, {'error_code': 'EXECUTION_SCOPE', 'uncertain': True}):
            with self.subTest(change=change):self.assertFalse(ok(dict(REJECTED, **change)))
        self.assertFalse(ok(REJECTED, files=[object()]))
        self.assertTrue(ok(dict(REJECTED, upstream_status=429)))

    def test_no_retry_after_stream_output(self):
        class P:seq = 1;parts = ['x'];events = [('text', 'x')]
        self.assertFalse(ConversationBridge.retry_eligible(REJECTED, P()))


if __name__ == '__main__':
    unittest.main()
