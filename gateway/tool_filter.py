"""Per-model client tool allowlist. Removes tool definitions before prompting; never executes tools.
Config: .console/tool-filter.json
  {"version": 1, "default": [...], "models": {"<alias>": "*" | [...]}}
Entries are exact names or fnmatch patterns (e.g. "mcp__amap__*"); "*" keeps every client tool.
A missing file means DEFAULT_TOOLS for every model. A malformed file blocks sending (fail closed).
"""
import json
import os
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path

# 基础开发工具：DSH dsh-tool-fs / fs-search / pwsh / todo / ask-user / web 的实际注册名
DEFAULT_TOOLS = ('read', 'write', 'edit', 'glob', 'grep', 'pwsh', 'bash',
                 'todo_write', 'ask_user_question', 'web_search', 'web_fetch', 'advanced_search',
                 # DSH 侧边栏：打开本地文件/目录或网页（客户端执行，不碰 Arena 云端）
                 'sidebar_open')


class ToolFilterError(ValueError):
    pass


def _valid_rule(rule):
    if rule == '*':
        return True
    return (isinstance(rule, list) and len(rule) <= 512
            and all(isinstance(x, str) and 0 < len(x) <= 128 for x in rule))


def validate(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ToolFilterError('工具白名单配置格式错误')
    default = data.get('default', list(DEFAULT_TOOLS))
    models = data.get('models', {})
    if not _valid_rule(default) or not isinstance(models, dict):
        raise ToolFilterError('工具白名单配置格式错误')
    for alias, rule in models.items():
        if not isinstance(alias, str) or not _valid_rule(rule):
            raise ToolFilterError('工具白名单配置格式错误')
    return {'version': 1, 'default': default, 'models': models}


def matches(name, rule):
    return rule == '*' or any(fnmatchcase(name, pattern) for pattern in rule)


class ToolFilter:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return {'version': 1, 'default': list(DEFAULT_TOOLS), 'models': {}}
        except OSError:
            raise ToolFilterError('工具白名单配置无法读取；未发送') from None
        try:
            return validate(json.loads(raw))
        except (ValueError, TypeError):
            raise ToolFilterError('工具白名单配置异常；未发送，请修复 .console/tool-filter.json') from None

    def rule_for(self, alias):
        data = self.load()
        return data['models'].get(alias, data['default'])

    def apply(self, alias, tools):
        """Return (kept, removed_names) preserving client order."""
        rule = self.rule_for(alias)
        kept, removed = [], []
        for tool in tools:
            name = tool['function']['name']
            if matches(name, rule):
                kept.append(tool)
            else:
                removed.append(name)
        return kept, removed

    def status(self):
        try:
            return {'error': False, 'configured': self.path.exists(), **self.load()}
        except ToolFilterError:
            return {'error': True, 'configured': True}

    def save(self, data):
        data = validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='tool-filter-', suffix='.tmp', delete=False) as f:
                temp = Path(f.name)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return self.status()
