"""Offline tests for the real startup gate; does not import a native GUI backend."""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))
import app


class StartupTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / '.test-tmp'
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        self.webview = types.ModuleType('webview')
        self.webview._state = {}
        self.webview.create_window = Mock()
        self.gui = types.ModuleType('webview.guilib')
        self.gui.initialize = Mock(return_value=types.SimpleNamespace(renderer='qtwebengine'))
        self.modules = patch.dict(sys.modules, {'webview': self.webview, 'webview.guilib': self.gui})
        self.modules.start()
        self.root_patch = patch.object(app, 'ROOT', self.root)
        self.root_patch.start()
        self.env = patch.dict(os.environ)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.root_patch.stop()
        self.modules.stop()
        self.tmp.cleanup()

    def test_backend_initialized_not_only_imported(self):
        before = os.environ.get('QTWEBENGINE_DISABLE_SANDBOX')
        result = app.desktop_backend_check()
        self.assertEqual(result.renderer, 'qtwebengine')
        self.gui.initialize.assert_called_once_with('qt')
        self.assertEqual(os.environ['QT_API'], 'pyside6')
        self.assertEqual(os.environ.get('QTWEBENGINE_DISABLE_SANDBOX'), before)

    def test_profile_stays_inside_project(self):
        app.desktop_backend_check()
        self.assertEqual(Path(self.webview._state['storage_path']), self.root / '.console' / 'webview')
        self.assertTrue((self.root / '.console' / 'webview').is_dir())

    def test_legacy_backend_not_silently_accepted(self):
        self.gui.initialize.return_value = types.SimpleNamespace(renderer='qtwebkit')
        with self.assertRaisesRegex(RuntimeError, 'Qt WebEngine backend unavailable'):
            app.desktop_backend_check()

    def test_backend_failure_propagates(self):
        self.gui.initialize.side_effect = RuntimeError('fixture native failure')
        with self.assertRaisesRegex(RuntimeError, 'fixture native failure'):
            app.desktop_backend_check()

    def test_check_mode_no_window_no_core(self):
        (self.root / 'desktop').mkdir()
        (self.root / 'desktop' / 'console.html').write_text('<html></html>')
        (self.root / 'server.py').write_text('# fixture')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['app.py', '--check']), patch.object(app, 'ConsoleCore') as core, contextlib.redirect_stdout(output):
            app.main()
        core.assert_not_called()
        self.webview.create_window.assert_not_called()
        self.gui.initialize.assert_called_once_with('qt')
        self.assertIn('initialized OK', output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
