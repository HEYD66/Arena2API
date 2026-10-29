"""Opt-in Windows native Qt WebEngine smoke check. Minimized auto-closing window, no gateway/account calls.
Run directly with the project's .venv Python. This is not a headless unit test.
"""
import json
import os
import threading
from pathlib import Path
import webview

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != 'nt':
        raise RuntimeError('Native desktop smoke check requires Windows')
    import sys
    sys.path.insert(0, str(ROOT / 'desktop'))
    from app import desktop_backend_check
    desktop_backend_check()
    called = threading.Event()
    result = {'ok': False, 'native_window': True, 'gateway_started': False, 'account_requests': 0}
    class Api:
        def ping(self):
            return 'bridge-ok'
        def report(self, value):
            if value == 'bridge-ok':
                called.set()
            return True
    html = '''<!doctype html><html><head><meta charset="utf-8"></head><body><div id="result">pending</div>
    <script>window.addEventListener('pywebviewready', async () => {
      const value = await window.pywebview.api.ping();
      document.getElementById('result').textContent = value;
      await window.pywebview.api.report(value);
    });</script></body></html>'''
    window = webview.create_window('Arena2API render check (auto close)', html=html, js_api=Api(), minimized=True, focus=False, width=400, height=250)
    def verify():
        try:
            if not window.events.loaded.wait(25):
                raise RuntimeError('Native document load timed out')
            if not called.wait(25):
                raise RuntimeError('Native Python bridge timed out')
            text = window.evaluate_js("document.getElementById('result').textContent")
            if text != 'bridge-ok':
                raise RuntimeError('Native JS/Python bridge result mismatch')
            result.update(ok=True, renderer='qtwebengine', bridge='roundtrip-ok')
        except Exception as exc:
            result['error_type'] = type(exc).__name__
            result['error'] = str(exc)[:300]
        finally:
            window.destroy()
    target = ROOT / '.console' / 'native-smoke-result.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    webview.start(verify, gui='qt', debug=False, private_mode=True, storage_path=str(ROOT / '.console' / 'smoke-webview'))
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if not result['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
