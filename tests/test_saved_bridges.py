"""Saved opt-in bridge orchestration; mocks only, no processes or account reads."""
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from core import ConsoleCore
T={'name':'fixture browser','cdp_port':36938}
U={'name':'another fixture','cdp_port':36939}

class SavedBridgeTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'.test-tmp';folder.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=folder);self.c=ConsoleCore(self.tmp.name)
        self.c.start=Mock();self.c.status=Mock(return_value={'compatible':True,'version':'0.5.0','send_mode_supported':True})
        self.c.workspace_status=Mock(return_value={'sources':[],'routes':[]})
    def tearDown(self):self.tmp.cleanup()
    def save(self,target=T):
        with patch.object(self.c,'_ensure_compat_bridge',return_value={'state':'starting'}):
            return self.c.compat_bridge(dict(target,confirmed=True))
    def external(self,ready=True,port=9090):
        suffix='-'+hashlib.sha256(('fixturebrowser|36938').encode()).hexdigest()[:12]
        base=self.c.root/'.console'/('local-cdp-bridge'+suffix);base.parent.mkdir(exist_ok=True)
        Path(str(base)+'.lock').write_text(json.dumps({'pid':1234}))
        Path(str(base)+'-status.json').write_text(json.dumps({'pid':1234,'protocol':2,'ready':ready,'instance':T['name'],'cdp_port':36938,'gateway_port':port,'updated_at':time.time()*1000}))
        self.c._pid_running=Mock(return_value=True);self.c._spawn=Mock(side_effect=AssertionError('no spawn'))
    def test_no_saved_target_no_bridge(self):
        with patch.object(self.c,'_ensure_compat_bridge') as ensure:
            self.c.workspace_start();ensure.assert_not_called()
    def test_saved_target_survives_restart_without_boot_action(self):
        self.save();other=ConsoleCore(self.tmp.name);other.start=Mock();other._ensure_compat_bridge=Mock()
        self.assertEqual(other.boot()['saved_bridges'],[T]);other.start.assert_not_called();other._ensure_compat_bridge.assert_not_called()
    def test_only_click_starts_saved_targets_after_gateway(self):
        self.save();self.save(U);order=[]
        self.c.start=Mock(side_effect=lambda:order.append('gateway'))
        self.c._ensure_compat_bridge=Mock(side_effect=lambda target:order.append(target))
        result=self.c.workspace_start();self.assertEqual(order,['gateway',T,U]);self.assertEqual(result['bridge_errors'],[])
    def test_requires_confirmation_and_valid_target(self):
        for data in (T,dict(T,confirmed=True,cdp_port=True),dict(T,confirmed=True,name='')):
            with self.assertRaises(ValueError):self.c.compat_bridge(data)
        self.c.start.assert_not_called();self.assertFalse(self.c.bridge_preferences_file.exists())
    def test_repeated_save_deduplicates_normalized_name(self):
        self.save();self.save(dict(T,name='fixturebrowser'))
        self.assertEqual(len(self.c.saved_bridges),1)
    def test_remove_does_not_stop_active_process(self):
        self.save();proc=Mock();proc.poll.return_value=None;self.c.compat_bridges[('fixturebrowser',36938,9090)]=proc
        self.c._terminate=Mock();self.c.start.reset_mock()
        self.c.compat_bridge(dict(T,action='remove',confirmed=True));self.c._terminate.assert_not_called();self.c.start.assert_not_called()
        self.assertEqual(ConsoleCore(self.tmp.name).saved_bridges,[])
    def test_partial_failure_does_not_skip_other_targets_or_retarget(self):
        self.save();self.save(U);self.c._ensure_compat_bridge=Mock(side_effect=[ValueError('fixture'),{}])
        result=self.c.workspace_start();self.assertEqual([x.args[0] for x in self.c._ensure_compat_bridge.call_args_list],[T,U])
        self.assertEqual(len(result['bridge_errors']),1);self.assertEqual(result['bridge_errors'][0]['name'],T['name'])
    def test_healthy_owned_process_is_reused(self):
        proc=Mock();proc.poll.return_value=None;self.c.compat_bridges[('fixturebrowser',36938,9090)]=proc
        self.c._spawn=Mock();self.assertTrue(self.c._ensure_compat_bridge(T)['owned']);self.c._spawn.assert_not_called()
    def test_healthy_external_process_is_reused_not_adopted(self):
        self.external();self.assertFalse(self.c._ensure_compat_bridge(T)['owned']);self.c._spawn.assert_not_called();self.assertEqual(self.c.compat_bridges,{})
    def test_external_unready_or_wrong_port_never_duplicates(self):
        for ready,port in ((False,9090),(True,9191)):
            self.external(ready,port)
            with self.assertRaises(ValueError):self.c._ensure_compat_bridge(T)
            self.c._spawn.assert_not_called()
    def test_corrupt_preferences_are_not_overwritten_or_executed(self):
        p=self.c.bridge_preferences_file;p.parent.mkdir(exist_ok=True);p.write_text('bad fixture json')
        other=ConsoleCore(self.tmp.name)
        with self.assertRaises(ValueError):other.compat_bridge(dict(T,confirmed=True))
        self.assertEqual(p.read_text(),'bad fixture json');self.assertEqual(other.saved_bridges,[])
    def test_failed_save_never_starts_or_changes_memory(self):
        self.c._save_json=Mock(side_effect=OSError('fixture'))
        with self.assertRaises(OSError):self.c.compat_bridge(dict(T,confirmed=True))
        self.c.start.assert_not_called();self.assertEqual(self.c.saved_bridges,[])
    def test_spawn_uses_only_explicit_target_and_gateway_port(self):
        proc=Mock();proc.stdout=[];proc.poll.return_value=None;self.c._spawn=Mock(return_value=proc)
        with patch('core.shutil.which',return_value='fixture-node'),patch('core.threading.Thread'):
            self.c._ensure_compat_bridge(U)
        args=self.c._spawn.call_args.args[0]
        self.assertEqual(args[-6:],['--instance',U['name'],'--cdp','36939','--gateway','9090'])
    def test_missing_dependency_preserves_consent_for_retry(self):
        with patch('core.shutil.which',return_value=None):
            with self.assertRaises(ValueError):self.c.compat_bridge(dict(T,confirmed=True))
        self.assertEqual(ConsoleCore(self.tmp.name).saved_bridges,[T])

if __name__=='__main__':unittest.main()
