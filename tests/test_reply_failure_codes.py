"""Offline safe completion-error codes; no browser/network access."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversation_stream import completion_failure
import gateway_log

class ReplyFailureCodes(unittest.TestCase):
    def test_receive_stages(self):
        for code in ('STREAM_CONNECT', 'STREAM_READ', 'STREAM_TIMEOUT', 'INTERNAL_ERROR'):
            self.assertIn(code, gateway_log.ERROR_CODES)
            error = completion_failure({'error_code': code, 'error': 'PRIVATE TOKEN'})
            self.assertEqual(error.status_code, 502)
            self.assertIn(code, error.detail)
            self.assertNotIn('PRIVATE', error.detail)
    def test_unknown_is_redacted(self):
        for code in ('Bearer PRIVATE TOKEN', {}, None, ['STREAM_READ']):
            error = completion_failure({'error_code': code})
            self.assertIn('OTHER', error.detail)
            self.assertNotIn('PRIVATE', error.detail)
    def test_old_extension_without_code(self):
        self.assertIn('OTHER', completion_failure({}).detail)

if __name__ == '__main__': unittest.main()
