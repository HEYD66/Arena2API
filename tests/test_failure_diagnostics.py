import sys,json,re,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gateway_log
from conversation_stream import completion_failure
class FailureDiagnosticsTests(unittest.TestCase):
    def test_pre_submission(self):
        text=completion_failure({'error_code':'VERIFICATION_REQUIRED','submission_attempted':False,'submission_accepted':False,'upstream_status':403,'failure_stage':2,'uncertain':False}).detail
        self.assertIn('提交本轮消息前失败',text);self.assertIn('获取会话授权',text);self.assertIn('403',text);self.assertIn('不代表原网页一定有验证码',text)
    def test_rejected_append(self):
        text=completion_failure({'error_code':'VERIFICATION_REQUIRED','submission_attempted':True,'submission_accepted':False,'upstream_status':403,'failure_stage':3,'uncertain':False}).detail
        self.assertIn('未被 Arena 接受',text);self.assertIn('提交消息',text)
    def test_dsh_does_not_classify_arena_rejection_as_auth(self):
        # DSH(pi-ai) classifyPiAiError: /\b(?:401|403)\b/ → AUTH（显示"API 密钥无效"）；/\b5\d\d\b/ → 可自动重试的 SERVER
        import re
        for status in (401,403,500,502):
            text=completion_failure({'error_code':'ACCESS_DENIED','submission_attempted':True,'submission_accepted':False,'upstream_status':status,'failure_stage':3,'uncertain':False}).detail
            self.assertIsNone(re.search(r'\b(?:401|403)\b',text),text);self.assertIsNone(re.search(r'\b5\d\d\b',text),text)
            self.assertIn('HTTP'+str(status),text)
        self.assertIn('刷新该 Arena 标签页',completion_failure({'error_code':'ACCESS_DENIED','submission_attempted':True,'submission_accepted':False,'upstream_status':403,'failure_stage':3}).detail)
    def test_unknown_result_not_unlocked(self):
        text=completion_failure({'error_code':'VERIFICATION_REQUIRED','uncertain':True,'submission_attempted':True,'upstream_status':403}).detail
        self.assertIn('保留待核对保护',text)
    def test_metadata_allowlist(self):
        self.assertEqual(gateway_log.failure_metadata({'failure_stage':'SECRET','upstream_status':True,'submission_accepted':1,'body':'SECRET','submission_attempted':False}),{'submission_attempted':False})
        self.assertEqual(gateway_log.failure_metadata({'failure_stage':9,'upstream_status':999}),{})
    def test_log_no_raw_failure_body(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'events.jsonl';sink=gateway_log.EventLog(p)
            sink.write({'trace':'a'*32,'event':'browser_result','error_code':'VERIFICATION_REQUIRED','body':'SECRET',**gateway_log.failure_metadata({'upstream_status':403,'failure_stage':3,'submission_attempted':True})})
            raw=p.read_text();self.assertNotIn('SECRET',raw);self.assertEqual(json.loads(raw)['failure_stage'],3)
    def test_legacy_error_does_not_invent_stage(self):
        text=completion_failure({'error_code':'VERIFICATION_REQUIRED'}).detail
        self.assertNotIn('阶段：',text);self.assertNotIn('提交本轮消息前失败',text)


class UpstreamHintTests(unittest.TestCase):
    # DSH(pi-ai) classifyPiAiError 的前几条规则：命中即被判成 AUTH / 可自动重试
    DSH = [re.compile(r'\b(?:401|403)\b'), re.compile(r'\b429\b|rate.?limit', re.I), re.compile(r'\b5\d\d\b')]
    def text(self, **kw):
        base = {'error_code': 'ACCESS_DENIED', 'submission_attempted': True, 'submission_accepted': False,
                'upstream_status': 403, 'failure_stage': 3, 'uncertain': False}
        return completion_failure(dict(base, **kw)).detail
    def test_every_hint_shown_in_chinese_and_never_matches_dsh_rules(self):
        for hint in sorted(gateway_log.UPSTREAM_HINTS):
            for status in (401, 403, 429, 502):
                with self.subTest(hint=hint, status=status):
                    t = self.text(upstream_hint=hint, upstream_status=status)
                    self.assertIn('响应类别：', t); self.assertNotIn(hint, t)
                    self.assertFalse(any(r.search(t) for r in self.DSH), t)
    def test_cloudflare_challenge_gets_specific_advice(self):
        self.assertIn('Cloudflare 验证页', self.text(upstream_hint='cloudflare_challenge'))
    def test_unknown_hint_dropped(self):
        self.assertEqual(gateway_log.failure_metadata({'upstream_hint': '<html>secret'}), {})
        self.assertNotIn('响应类别', self.text(upstream_hint='<html>secret'))
    def test_retried_wording(self):
        self.assertIn('已自动重试一次仍被拒', self.text(retried=True))
