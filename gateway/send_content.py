"""Select the current request without inferring cross-client history or replaying it.
Input must already have passed normalize_request (including tool pairing checks).
"""
import json
from openai_compat import ProtocolError

# 回传工具结果时，工具调用里的大段字符串（如写文件的完整 HTML）不再原样重发：
# 模型刚生成过、网页会话里已有，重发只会让提交体变大，还可能触发 Arena/Cloudflare 的 403。
ARG_KEEP_CHARS = 2000


def _compact_call(call):
    try:
        args = json.loads(call['function']['arguments'])
    except (KeyError, TypeError, ValueError):
        return call
    if not isinstance(args, dict):
        return call
    changed = False
    for key, value in args.items():
        if isinstance(value, str) and len(value) > ARG_KEEP_CHARS:
            args[key] = '[已省略 ' + str(len(value)) + ' 字符：该参数是你上一轮生成的内容，客户端已按原文执行]'
            changed = True
    if not changed:
        return call
    return {**call, 'function': {**call['function'], 'arguments': json.dumps(args, ensure_ascii=False)}}


def select_current_messages(messages):
    last = messages[-1]
    if last['role']=='user' and last.get('_client_tool_image_bridge') and len(messages)>1 and messages[-2]['role']=='tool':
        # Preserve the complete immediately preceding tool batch, not earlier history.
        return [*select_current_messages(messages[:-1]), last]
    if last['role'] == 'user':
        # One text message, not the whole last user/assistant/tool loop.
        return [{'role': 'user', 'content': last['content'], **({'_client_files':last['_client_files']} if last.get('_client_files') else {})}]
    if last['role'] == 'tool':
        start = len(messages) - 1
        while start >= 0 and messages[start]['role'] == 'tool':
            start -= 1
        if start >= 0 and messages[start]['role'] == 'assistant' and messages[start].get('tool_calls'):
            # Keep exact call IDs/names/arguments plus ALL results of this batch.
            # Prior assistant prose, older user turns and previous tool batches are not resent.
            return [{'role': 'assistant', 'content': None, 'tool_calls': [_compact_call(c) for c in messages[start]['tool_calls']]},
                    *messages[start + 1:]]
    raise ProtocolError('正常续聊要求请求以用户消息或完整的一批工具结果结束；不能猜测要重发哪一条。需要发送完整客户端消息时请选择携带客户端上下文。', 'messages')
