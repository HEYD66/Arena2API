"""Live, bounded OpenAI SSE forwarding for one pinned existing conversation.
Tool requests are emitted ONLY after the existing full-response validator succeeds.
No polling of Arena, synthetic text animation, or send retries.
"""
import asyncio
import gateway_log
import json
import re
import time
import uuid
import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openai_compat import ProtocolError, parse_calls


def completion_failure(result):
    code = result.get('error_code')
    safe = code if isinstance(code, str) and code in gateway_log.ERROR_CODES else 'OTHER'
    meta=gateway_log.failure_metadata(result)
    stages={1:'读取会话',2:'获取会话授权',3:'提交消息',4:'读取回复',5:'上传附件',6:'核对状态',7:'停止请求'}
    context=('；阶段：'+stages[meta['failure_stage']]) if 'failure_stage' in meta else ''
    # DSH(pi-ai) 用 /\b(?:401|403)\b/ 把错误判成 AUTH 并显示"API 密钥无效"，用 /\b5\d\d\b/ 判成可自动重试的 SERVER。
    # 状态码紧贴 "HTTP" 写成 HTTP403，没有单词边界，DSH 就会原样显示这段文字，也不会自动重试。
    status=meta.get('upstream_status')
    if status is not None:context+='；Arena 上游 HTTP'+str(status)
    hints={401:'Arena 网页登录可能已失效，请在该标签页确认已登录',
           403:'Arena 拒绝了请求，多为 Cloudflare 验证、提交过快或网页登录过期；请刷新该 Arena 标签页后再发',
           429:'Arena 提示提交过于频繁，请稍等再发'}
    # 扩展从上游响应里归出的固定类别（不含原文），比按状态码猜更准
    kinds={'cloudflare_challenge':'Arena 返回的是 Cloudflare 验证页；请在该标签页刷新并通过验证后再发',
           'cloudflare_block':'Arena 返回的是 Cloudflare 拦截页；请稍等几分钟或刷新该标签页后再发',
           'captcha':'Arena 要求人机验证；请在该标签页完成验证后再发',
           'rate_limit':'Arena 提示提交过于频繁，请稍等再发',
           'login':'Arena 网页登录可能已失效，请在该标签页确认已登录'}
    hint=meta.get('upstream_hint')
    # 类别用中文显示：英文名 rate_limit 会命中 DSH 的 /rate.?limit/ 被判成可自动重试
    labels={'empty':'空响应','cloudflare_challenge':'Cloudflare验证页','cloudflare_block':'Cloudflare拦截页','captcha':'人机验证',
            'rate_limit':'频率限制','login':'登录失效','json_error':'接口错误','html_page':'网页错误页','other':'其他'}
    if hint in labels:context+='；响应类别：'+labels[hint]
    retried=result.get('retried') is True
    if result.get('uncertain') is True:
        message='本轮提交或完成状态未确认；保留待核对保护，不自动重发'
    elif meta.get('submission_attempted') is False:
        message='扩展请求在提交本轮消息前失败；未自动重发'
    elif meta.get('submission_accepted') is False and 400<=(status or 0)<500 and status!=408:
        message='本轮消息未被 Arena 接受，网关已自动重试一次仍被拒' if retried else '本轮消息未被 Arena 接受，网关没有自动重发'
    else:
        message='当前对话未返回已确认完成的文本；不自动重发'
    if safe=='VERIFICATION_REQUIRED':
        message+='。扩展请求被归类为验证相关错误，不代表原网页一定有验证码；仅在网页确有提示时处理验证'
    elif result.get('uncertain') is not True and hint in kinds:
        message+='。'+kinds[hint]
    elif status in hints and result.get('uncertain') is not True:
        message+='。'+hints[status]
    return HTTPException(502, message+'（'+safe+context+'）')


class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI >=2.4 reports disconnect via send() OSError rather than cancelling
            # the iterator. Explicitly close it so a detached send task cannot survive.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def units(text):
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


ACTIVITY_LABELS = {
    'submitted': '本轮消息已确认提交，等待上游事件',
    'thinking': '网页正在思考（收到新的思考事件）',
    'thought': '网页当前思考片段已结束',
    'tool_input': '网页工具正在接收参数',
    'tool_running': '网页工具等待返回',
    'tool_returned': '网页工具已返回',
    'tool_error': '网页工具返回错误',
    'generating': '正在接收模型回复；工具调用仍待完整校验',
    'received': '网页本轮已结束，等待网关校验',
}


# 只有这几类网页工具事件会显示（且必须带工具名）；其余 ACTIVITY_LABELS 仅用于校验
WEB_TOOL_STATES = {'tool_input': '接收参数', 'tool_running': '执行中', 'tool_returned': '已返回', 'tool_error': '返回错误'}

REASONING_LIMIT = 200000

# 工具调用摘要：只取工具名和一个短的定位参数（路径/命令/搜索词），不含文件正文和工具返回
STEP_NAMES = {
    'read': '读文件', 'read_image': '看图片', 'write': '写文件', 'edit': '改文件', 'str_replace_editor': '改文件',
    'glob': '找文件', 'grep': '搜索代码', 'pwsh': '命令', 'bash': '命令', 'todo_write': '任务列表',
    'ask_user_question': '提问', 'web_search': '搜索网页', 'web_fetch': '读网页', 'advanced_search': '搜索网页',
}
STEP_KEYS = ('command', 'path', 'file_path', 'pattern', 'query', 'url')
_STEP_NAME = re.compile(r'"name"\s*:\s*"([A-Za-z0-9_.-]{1,64})"')
_STEP_ARG = re.compile(r'"(' + '|'.join(STEP_KEYS) + r')"\s*:\s*"((?:[^"\\]|\\.){0,400})"')


def _clean_arg(raw):
    try:
        value = json.loads('"' + raw + '"')
    except ValueError:
        value = raw
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value).strip()
    return value if len(value) <= 120 else value[:120] + '…'


class ToolSteps:
    """从流式正文里的 <tool_call> 块实时提取步骤摘要。只读展示，不产生可执行调用。"""

    def __init__(self):
        self.text = ''
        self.scanned = 0   # 已处理完的块之后的位置
        self.announced = set()
        self.count = 0

    def feed(self, delta):
        if len(self.text) < 2000000:
            self.text += delta
        out = []
        while True:
            start = self.text.find('<tool_call>', self.scanned)
            if start < 0:
                break
            end = self.text.find('</tool_call>', start)
            block = self.text[start:end if end >= 0 else len(self.text)]
            if start not in self.announced and self.count < 64:
                name = _STEP_NAME.search(block)
                arg = _STEP_ARG.search(block)
                if name and (arg or end >= 0):
                    self.announced.add(start)
                    self.count += 1
                    label = STEP_NAMES.get(name.group(1), '工具 ' + name.group(1))
                    out.append('▸ ' + label + (' · ' + _clean_arg(arg.group(2)) if arg else ''))
            if end < 0:
                break
            self.scanned = end + len('</tool_call>')
        return out


class LiveReply:
    def __init__(self, allow_activity=False):
        self.parts = []
        self.events = []
        self.allow_activity = allow_activity
        self.activity_count = 0
        self.reasoning_size = 0
        self.seq = 0
        self.size = 0
        self.changed = asyncio.Event()
        self.failure = None

    def push(self, progress):
        seq, delta = progress.get('seq'), progress.get('delta')
        if type(seq) is not int or seq < 1 or not isinstance(delta, str) or not delta or units(delta) > 16000:
            raise HTTPException(400, '流式增量格式或大小无效')
        if seq <= self.seq:
            return  # RESULT/progress delivery may be retried; never replay text.
        if seq != self.seq + 1:
            raise HTTPException(409, '流式增量序号不连续；等待最终结果核对')
        kind = progress.get('kind', 'text')
        if kind == 'reasoning':
            # 网页思考原文：只进 reasoning_content，不计入正文、不参与工具调用解析
            if not self.allow_activity:
                raise HTTPException(400, '当前请求未启用进度')
            self.seq = seq
            if self.reasoning_size + units(delta) <= REASONING_LIMIT:
                self.reasoning_size += units(delta)
                self.events.append(('reasoning', delta))
                self.changed.set()
            return
        if kind == 'activity':
            tool = progress.get('tool', '')
            if (not self.allow_activity or delta not in ACTIVITY_LABELS or not isinstance(tool, str)
                    or (tool and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,79}', tool))):
                raise HTTPException(400, '进度事件无效或当前请求未启用进度')
            self.seq = seq
            # "已提交/正在思考/已结束"这类固定状态没有信息量，只会刷屏，不再显示。
            # 只保留带工具名的网页工具事件（属于 Arena 云端执行，单独标明）。
            if delta not in WEB_TOOL_STATES or not tool:
                return
            if self.activity_count < 256:
                self.activity_count += 1
                self.events.append(('activity', '\n▸ 网页工具 · ' + tool + ' · ' + WEB_TOOL_STATES[delta] + '\n'))
                self.changed.set()
            return
        if kind != 'text':
            raise HTTPException(400, '未知的增量类型')
        self.size += units(delta)
        if self.size > 1000000:
            self.failure = HTTPException(502, '流式回复超出本地接收上限；请核对原页面')
            self.changed.set()
            raise self.failure
        self.seq = seq
        self.parts.append(delta)
        self.events.append(('text', delta))
        self.changed.set()


class TextGate:
    """Hold tag prefixes across chunk boundaries; never stream executable XML as text."""
    MARKERS = ('<tool_call>', '</tool_call>')

    def __init__(self, policy):
        self.active = policy.active
        self.held = ''
        self.blocked = policy.active and policy.mode in ('required', 'selected')

    def feed(self, delta):
        if not self.active:
            return delta
        self.held += delta
        if self.blocked:
            return ''
        positions = [self.held.find(x) for x in self.MARKERS if x in self.held]
        if positions:
            cut = min(positions)
            out, self.held = self.held[:cut], self.held[cut:]
            self.blocked = True
            return out
        keep = max((n for marker in self.MARKERS for n in range(1, len(marker)) if self.held.endswith(marker[:n])), default=0)
        out = self.held[:-keep] if keep else self.held
        self.held = self.held[-keep:] if keep else ''
        return out


def live_response(bridge, prompt, revision, policy, agent, *, heartbeat=10, execution_policy=None, files=None):
    state = LiveReply(bool(execution_policy and execution_policy.get('activity_stream')))
    ident, created = 'chatcmpl-' + uuid.uuid4().hex, int(time.time())

    def chunk(delta, finish=None):
        return 'data: ' + json.dumps({'id': ident, 'object': 'chat.completion.chunk', 'created': created,
            'model': getattr(bridge,'model_alias','arena-bound'), 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n'

    async def events():
        work = None
        gate = TextGate(policy)
        consumed = 0
        outcome = 'pending'
        first_visible = False
        steps = ToolSteps() if state.allow_activity else None
        in_reasoning, step_no = False, 0
        gateway_log.emit('stream_started')
        try:
            # Started only when StreamingResponse is consumed, not when merely constructed.
            # Starlette owns disconnect monitoring while this iterator is running.
            work = asyncio.create_task(bridge.queued_send(prompt, revision, progress=state, execution_policy=execution_policy, **({'files':files} if files else {})))
            work.add_done_callback(lambda _: state.changed.set())
            yield chunk({'role': 'assistant'})
            while True:
                if state.failure:
                    raise state.failure
                while consumed < len(state.events):
                    kind, value = state.events[consumed]
                    consumed += 1
                    if kind == 'reasoning':
                        # 连续的思考片段归为一步，按插件格式加步骤标题
                        if not in_reasoning:
                            in_reasoning = True
                            step_no += 1
                            yield chunk({'reasoning_content': '\n思考过程 · 第 ' + str(step_no) + ' 步\n'})
                        yield chunk({'reasoning_content': value})
                        continue
                    in_reasoning = False
                    if kind == 'activity':
                        yield chunk({'reasoning_content': value})
                        continue
                    if steps is not None:
                        for line in steps.feed(value):
                            yield chunk({'reasoning_content': '\n' + line + '\n'})
                    visible = gate.feed(value)
                    if visible:
                        if not first_visible:
                            gateway_log.emit('sse_content');first_visible=True
                        yield chunk({'content': visible})
                if work.done():
                    break
                state.changed.clear()
                try:
                    await asyncio.wait_for(state.changed.wait(), heartbeat)
                except asyncio.TimeoutError:
                    yield ': keep-alive\n\n'  # Never fabricate assistant tokens while queued/thinking.
            result = await work
            if result.get('ok') is not True or not isinstance(result.get('text'), str) or not result['text'].strip() or result.get('completed') is False:
                raise completion_failure(result)
            final = result['text']
            if units(final) > 1000000:
                raise HTTPException(502, '最终回复超出本地接收上限')
            streamed = ''.join(state.parts)
            if not final.startswith(streamed):
                bridge.uncertain = True
                raise HTTPException(502, '增量与最终回复不一致；结果待核对，不自动重发')
            calls = parse_calls(final, policy, 'stop') if agent else None
            # A lost progress delivery or old extension can be reconciled from the final
            # authoritative reply. Already-delivered text is never repeated.
            remainder = final[len(streamed):]
            if steps is not None and remainder:
                # 进度增量丢失时，用最终回复补齐步骤摘要
                for line in steps.feed(remainder):
                    yield chunk({'reasoning_content': '\n' + line + '\n'})
            visible = gate.feed(remainder)
            if visible:
                if not first_visible:
                    gateway_log.emit('sse_content');first_visible=True
                yield chunk({'content': visible})
            if calls:
                yield chunk({'tool_calls': [dict(call, index=i) for i, call in enumerate(calls)]})
            elif gate.held:
                yield chunk({'content': gate.held})
            yield chunk({}, 'tool_calls' if calls else 'stop')
            yield 'data: [DONE]\n\n'
            outcome = 'success'
            gateway_log.emit('stream_success',tools=len(calls or []))
        except (HTTPException, ProtocolError) as error:
            outcome = 'error'
            gateway_log.emit('stream_error',error_status=error.status if isinstance(error,ProtocolError) else error.status_code)
            if isinstance(error, ProtocolError):
                payload = error.payload()
            else:
                payload = {'error': {'message': str(error.detail), 'type': 'server_error' if error.status_code >= 500 else 'invalid_request_error', 'code': str(error.status_code), 'param': None}}
            yield 'data: ' + json.dumps(payload) + '\n\n'
            yield 'data: [DONE]\n\n'  # No successful finish_reason on failure.
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = 'error'
            gateway_log.emit('stream_error',error_status=500)
            bridge.uncertain = True
            yield 'data: ' + json.dumps({'error': {'message': '流式通道异常；请核对原页面，不自动重发', 'type': 'server_error', 'code': 'stream_failed', 'param': None}}) + '\n\n'
            yield 'data: [DONE]\n\n'
        finally:
            if work is not None:
                if outcome == 'pending' and work.done() and not work.cancelled() and work.exception() is None:
                    result = work.result()
                    if result.get('ok') is True:
                        # Arena finished, but HTTP delivery was abandoned mid-stream.
                        bridge.uncertain = True
                if not work.done():
                    work.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(work, return_exceptions=True)
            state.parts.clear()

    return ClosingStreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
