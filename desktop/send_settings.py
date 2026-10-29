"""Project-local gateway sending mode. No credentials or conversation state."""
import json
import os
import tempfile
from pathlib import Path

MODES = ('normal', 'full')

class SendModeSettings:
    def __init__(self, path):
        self.path = Path(path)

    def get_mode(self):
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return 'normal'
        except OSError:
            raise ValueError('发送模式配置无法读取；未发送，请检查项目内配置') from None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('mode') not in MODES:
                raise ValueError()
            return data['mode']
        except (ValueError, TypeError):
            raise ValueError('发送模式配置异常；未发送，请在控制台重新保存发送模式') from None

    def status(self):
        try:
            return {'mode': self.get_mode(), 'error': False}
        except ValueError:
            return {'mode': None, 'error': True}

    def save(self, mode):
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError('发送模式只能选择正常续聊或携带客户端上下文')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='send-mode-', suffix='.tmp', delete=False) as f:
                temp = Path(f.name)
                json.dump({'mode': mode}, f)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return self.status()
