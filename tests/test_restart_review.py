"""Legacy restart review: no browser, account, network or production config."""
import sys, json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from source_router import SourceRouter
from fastapi import HTTPException
A={'client_id':'a'*32,'session':'1'*32,'account_key':'a'*64,'label':'fixture','source_label':'fixture'}
ROW=dict(alias='fixture',client_id=A['client_id'],account_key=A['account_key'],context_key='https://arena.ai/agent/11111111-1111-4111-8111-111111111111',title='fixture',allow_clients=True,pending_review=True,tool_reference=True)
class ReviewTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'routes.json';self.path.write_text(json.dumps([ROW]));self.r=SourceRouter(self.path);self.r.offer(A)
 def tearDown(self):self.tmp.cleanup()
 async def act(self,action='check',**kw):return await self.r.control(dict(alias='fixture',action=action,confirmed=True,**kw))
 async def challenge(self):
  result=await self.act();self.assertFalse(result['resolved']);self.assertTrue(result['review_required']);self.assertTrue(self.r.routes['fixture']['pending_review']);self.assertTrue(self.r.queues[A['client_id']].empty());return result['review_token']
 async def test_restart_can_review_without_enabling(self):
  row=self.r.status()['routes'][0];self.assertFalse(row['online']);self.assertTrue(row['review_available']);await self.challenge();self.assertFalse(self.r.routes['fixture']['enabled'])
 async def test_ack_is_manual_not_completion_or_restore(self):
  token=await self.challenge();result=await self.act('acknowledge_review',acknowledged=True,review_token=token)
  self.assertTrue(result['review_acknowledged']);self.assertFalse(result['resolved']);self.assertFalse(result['enabled']);self.assertFalse(result['replayed'])
  loaded=SourceRouter(self.path);row=loaded.routes['fixture'];self.assertFalse(row['pending_review']);self.assertFalse(row['enabled']);self.assertTrue(row['tool_reference']);self.assertEqual(row['context_key'],ROW['context_key']);self.assertTrue(self.r.queues[A['client_id']].empty())
 async def test_no_ack_without_confirm_and_token(self):
  for kw in [{},{'acknowledged':True},{'acknowledged':True,'review_token':'bogus'}]:
   with self.assertRaises(HTTPException):await self.act('acknowledge_review',**kw)
  self.assertTrue(self.r.routes['fixture']['pending_review'])
 async def test_cancel_does_not_mutate_disk(self):
  before=self.path.read_bytes();await self.challenge();self.assertEqual(before,self.path.read_bytes())
 async def test_replay_rejected(self):
  t=await self.challenge();await self.act('acknowledge_review',acknowledged=True,review_token=t)
  with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
 async def test_expiry_rejected(self):
  t=await self.challenge();self.r.review_challenges['fixture']['expires']=0
  with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
 async def test_source_session_change_rejected(self):
  t=await self.challenge();self.r.sources[A['client_id']]['session']='2'*32
  with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
 async def test_account_switch_rejected(self):
  t=await self.challenge();self.r.sources[A['client_id']]['account_key']='b'*64
  self.assertFalse(self.r.status()['routes'][0]['review_available'])
  with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
 async def test_offline_source_rejected(self):
  self.r.sources[A['client_id']]['seen']=0;self.assertFalse(self.r.status()['routes'][0]['review_available'])
  with self.assertRaises(HTTPException):await self.challenge()
 async def test_replaced_binding_rejected(self):
  t=await self.challenge();self.r.routes['fixture']=dict(self.r.routes['fixture'])
  with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
 async def test_busy_lane_rejected(self):
  t=await self.challenge();await self.r.lanes[A['client_id']].acquire()
  try:
   self.assertFalse(self.r.status()['routes'][0]['review_available'])
   with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
  finally:self.r.lanes[A['client_id']].release()
 async def test_save_failure_retains_lock(self):
  t=await self.challenge();before=self.path.read_bytes()
  with patch.object(self.r,'save',side_effect=OSError('fixture')):
   with self.assertRaises(HTTPException):await self.act('acknowledge_review',acknowledged=True,review_token=t)
  self.assertTrue(self.r.routes['fixture']['pending_review']);self.assertEqual(before,self.path.read_bytes())
 async def test_other_alias_unchanged(self):
  self.r.routes['other']=dict(ROW,alias='other',enabled=False);before=dict(self.r.routes['other']);t=await self.challenge();await self.act('acknowledge_review',acknowledged=True,review_token=t);self.assertEqual(before,self.r.routes['other'])
 async def test_legacy_restore_still_blocked_until_review(self):
  with self.assertRaises(HTTPException):await self.r.restore({'alias':'fixture','confirmed':True})
  self.assertTrue(self.r.routes['fixture']['pending_review'])
if __name__=='__main__':unittest.main()
