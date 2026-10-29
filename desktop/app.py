"""Native Qt WebEngine desktop shell. Only the explicit bridge below is exposed."""
import json
import os
import sys
import traceback
import webbrowser
from pathlib import Path

from core import ConsoleCore

ROOT = Path(__file__).resolve().parents[1]


class Api:
    def __init__(self, core):
        self._core = core

    def _run(self, fn, *args):
        try:
            return {'ok': True, 'data': fn(*args)}
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        except Exception:
            self._core.add_log('error', '控制台操作失败。请检查本地环境；没有自动重试。')
            return {'ok': False, 'error': '操作未完成，请检查本地环境后重试。'}

    def boot(self):
        return self._run(self._core.boot)

    def status(self):
        return self._run(self._core.status)

    def conversation(self, action, data=None):
        return self._run(self._core.conversation, action, data)

    def history(self, data=None):
        return self._run(self._core.history, data)

    def connect(self, data=None):
        return self._run(self._core.connect, data)

    def start(self):
        return self._run(self._core.start)

    def stop(self, confirmed=False):
        return self._run(self._core.stop, confirmed)

    def save_send_mode(self,data):
        return self._run(self._core.save_send_mode,data)

    def save_config(self, data):
        return self._run(self._core.save_config, data)

    def logs(self):
        return self._run(self._core.get_logs)

    def workspace_status(self):
        return self._run(self._core.workspace_status)
    def workspace_start(self):
        return self._run(self._core.workspace_start)
    def workspace_action(self,action,data=None):
        return self._run(self._core.workspace_action,action,data)
    def compat_bridge(self,data):
        return self._run(self._core.compat_bridge,data)

    def gateway_logs(self):
        return self._run(self._core.gateway_logs)

    def clear_logs(self):
        return self._run(self._core.clear_logs)

    def open_item(self, item):
        def action():
            if item == 'arena':
                webbrowser.open('https://arena.ai')
            else:
                paths = {'extension': ROOT / 'extension' / 'chrome',
                         'guide': ROOT / '多账号会话路由说明.md', 'project': ROOT}
                if item not in paths:
                    raise ValueError('不支持的打开操作')
                if os.name != 'nt':
                    raise ValueError('此快捷入口仅在 Windows 桌面版可用')
                os.startfile(str(paths[item]))
            return True
        return self._run(action)


def configure_desktop_runtime():
    os.environ['QT_API'] = 'pyside6'
    # Distribution: preserve Qt WebEngine sandbox defaults; no local bypass setting.


def desktop_backend_check():
    # Pin the binding and its profile directory before importing the Qt backend.
    configure_desktop_runtime()
    import webview
    storage = ROOT / '.console' / 'webview'
    storage.mkdir(parents=True, exist_ok=True)
    webview._state['storage_path'] = str(storage)
    from webview.guilib import initialize
    backend = initialize('qt')
    if getattr(backend, 'renderer', None) != 'qtwebengine':
        raise RuntimeError('Qt WebEngine backend unavailable; legacy WebKit is not supported')
    return backend


def main():
    configure_desktop_runtime()
    import webview
    if '--check' in sys.argv:
        assert (ROOT / 'desktop' / 'console.html').is_file()
        assert (ROOT / 'server.py').is_file()
        desktop_backend_check()
        print('Desktop Qt WebEngine backend initialized OK; no window/server/account requests started.')
        return
    core = ConsoleCore(ROOT)
    api = Api(core)
    html = (ROOT / 'desktop' / 'console.html').read_text(encoding='utf-8')
    window = webview.create_window('Arena2API · 账号工作台 0.6.0', html=html, js_api=api,
                                  width=1240, height=840, min_size=(980, 700),
                                  background_color='#101010', text_select=True)

    def before_close():
        owned = core.proc is not None and core.proc.poll() is None
        busy = core.test_lock.locked()
        if owned or busy or (core.bridge_proc is not None and core.bridge_proc.poll() is None) or any(p.poll() is None for p in core.compat_bridges.values()):
            if not window.create_confirmation_dialog('关闭工作台',
                    '关闭将停止本窗口启动的网关，和本机桥。DSH 中的请求也可能中断；Arena 上已提交的任务未必停止。确定关闭？'):
                return False
        core.close()
        return True

    window.events.closing += before_close
    webview.start(gui='qt', debug=False,
                  private_mode=True, storage_path=str(ROOT / '.console' / 'webview'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        report = ROOT / '.console' / 'startup-error.txt'
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass
        if os.name == 'nt' and '--check' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                '桌面窗口启动失败（Qt WebEngine）。\n\n' + type(exc).__name__ + ': ' + str(exc)[:450] + '\n\n详细诊断：' + str(report),
                'Arena2API', 0x10)
        raise
