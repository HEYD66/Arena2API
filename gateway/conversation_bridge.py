"""Explicitly bound existing Arena conversations. Memory only; no account HTTP here."""
from client_attachments import collect_files, descriptor, AttachmentError, CHUNK_CHARS
import secrets
import asyncio
import gateway_log
import copy
import json
import re
import time
import uuid
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from send_content import select_current_messages
from tool_audit import begin_audit
from tool_reference import render_reference_prompt
from conversation_catalog import ConversationCatalog
from conversation_stream import live_response, completion_failure
from openai_compat import normalize_request, render_agent_prompt, parse_calls, ProtocolError

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
CONTEXT = re.compile(r'^https://arena\.ai/(agent|work)/(' + UUID + r')$')
CLIENT = re.compile(r'^[0-9a-f]{32}$')

# Same local ceiling as the extension chat core; not an upstream context guarantee.
BOUND_PROMPT_LIMIT = 200000

def prompt_units(text):
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


DSH_TITLE_SYSTEM = ('Create a concise title for an AI coding-assistant session from the supplied human messages. '
    'Return only the title on one line, in plain text of natural language, with no quotes, prefix, explanation, Markdown, XML, or terminal control codes. '
    'No code is allowed. Use the language of the messages. Aim for about 5 words in non-CJK languages or 10 CJK characters.')
DSH_TITLE_PREFIX = 'Generate the session title from this JSON array of human messages:'


def local_dsh_title(body, policy):
    """Exact known auxiliary template only; deterministic local label, not model output."""
    messages = body['messages']
    if policy.tools or len(messages) != 2 or [m['role'] for m in messages] != ['system', 'user']:
        return None
    if ' '.join(messages[0]['content'].split()) != DSH_TITLE_SYSTEM:
        return None
    text = messages[1]['content']
    if not text.startswith(DSH_TITLE_PREFIX):
        return None
    try:
        rows = json.loads(text[len(DSH_TITLE_PREFIX):].strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        return None
    if any(not isinstance(r, dict) or set(r) != {'seq', 'text'} or type(r['seq']) is not int or not isinstance(r['text'], str) for r in rows):
        return None
    source = next((r['text'] for r in rows if r['text'].strip()), '')
    clean = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in source)
    clean = ' '.join(clean.split())
    if re.search(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]', clean):
        return clean[:10].strip() or 'New session'
    return ' '.join(clean.split()[:5])[:80].strip() or 'New session'


class ConversationBridge(ConversationCatalog):
    def __init__(self):
        self.offers = {}
        self.catalog_cache = {}
        self.queues = {}
        self.binding = None
        self.pending = {}
        self.busy = False
        self.uncertain = False
        self.completion_gate = asyncio.Lock()
        self.idle = asyncio.Event()
        self.idle.set()
        self.waiting = 0
        self.max_waiting = 4
        self.queue_timeout = 60
        self.last_tool_audit = None
        self.tool_audit_sequence = 0
        self.retry_delay = 4

    @staticmethod
    def retry_eligible(result, progress=None, files=None):
        """只有 Arena 在提交阶段明确拒收（消息没进会话、结果确定）才允许重试一次。"""
        if not isinstance(result, dict) or result.get('ok') is True or files:
            return False
        if result.get('uncertain') is not False or result.get('submission_attempted') is not True:
            return False
        if result.get('submission_accepted') is not False or result.get('failure_stage') != 3:
            return False
        if result.get('upstream_status') not in (403, 429):
            return False
        # 流式请求已经向客户端发过任何内容，就不能重来，否则会重复输出
        if progress is not None and (progress.seq or progress.parts or progress.events):
            return False
        return True

    def offer(self, data):
        client, key = data.get('client_id', ''), data.get('context_key', '')
        if not isinstance(client, str) or not CLIENT.fullmatch(client):
            raise HTTPException(400, '扩展实例标识无效')
        catalog = data.get('catalog') is True and isinstance(data.get('scope_id'), str) and bool(re.fullmatch(r'[0-9a-f]{64}',data['scope_id']))
        if not isinstance(key, str) or not (CONTEXT.fullmatch(key) or catalog and key == 'https://arena.ai'):
            raise HTTPException(400, '请打开已有的 Arena Agent/Work 对话')
        for old in list(self.offers):
            if time.time()-self.offers[old]['seen'] > 300 and (not self.binding or old != self.binding['client_id']):
                self.offers.pop(old, None)
                self.queues.pop(old, None)
        if client not in self.offers and len(self.offers) >= 32:
            raise HTTPException(409, '候选扩展过多，请重启网关清理')
        self.offers[client] = {'client_id': client, 'context_key': key,
                               'title': str(data.get('title') or '当前对话')[:160], 'seen': time.time(),
                               'catalog': catalog, 'scope_id':data.get('scope_id') if catalog else None,
                               'source_label':'实例 5' if catalog else ''}
        self.queues.setdefault(client, asyncio.Queue(maxsize=2))
        return {'ok': True}

    def live(self):
        if not self.binding:
            return False
        offer = self.offers.get(self.binding['client_id'])
        return bool(offer and offer['context_key'] == self.binding['context_key'] and time.time()-offer['seen'] < 45)

    def status(self):
        offers = [dict(x, online=time.time()-x['seen'] < 45) for x in self.offers.values()]
        return {'offers': offers, 'sources': self.catalog_sources(), 'binding': copy.deepcopy(self.binding), 'online': self.live(),
                'busy': self.busy, 'uncertain': self.uncertain, 'waiting': self.waiting, 'tool_audit': copy.deepcopy(self.last_tool_audit), 'model_alias': 'arena-bound'}

    def bind(self, data):
        if self.busy or self.uncertain:
            raise HTTPException(409, '当前操作进行中或结果未知，请先核对原对话')
        if data.get('confirmed') is not True:
            raise HTTPException(400, '绑定或解除绑定需要确认')
        if data.get('unbind') is True:
            self.binding = None
            return self.status()
        offer = self.offers.get(data.get('client_id'))
        if not offer or offer['context_key'] != data.get('context_key') or time.time()-offer['seen'] >= 45:
            raise HTTPException(409, '候选对话已变化或离线，请重新选择')
        if not CONTEXT.fullmatch(offer['context_key']):
            raise HTTPException(400, '请选择一个历史对话，而不是账号首页')
        self.binding = {k: offer[k] for k in ('client_id', 'context_key', 'title')}
        self.binding.update(revision=uuid.uuid4().hex, allow_clients=data.get('allow_clients') is True)
        return self.status()

    async def command(self, client):
        if client not in self.queues:
            raise HTTPException(404, '请先在扩展选择对话')
        try:
            cmd = await asyncio.wait_for(self.queues[client].get(), 20)
            if cmd['request_id'] not in self.pending or cmd['expires_at'] < time.time()*1000:
                item=self.pending.get(cmd['request_id'])
                if item and item.get('trace'):item['trace'].event('command_expired')
                if item and not item['future'].done():
                    item['future'].set_result({'ok': False, 'completed': False, 'uncertain': False, 'error_code': 'TIMEOUT', 'error': '操作在交付浏览器前已过期；未执行、未重发。'})
                return {'kind': 'idle'}
            item=self.pending.get(cmd['request_id'])
            if item and item.get('trace'):item['trace'].event('command_taken')
            return cmd
        except asyncio.TimeoutError:
            return {'kind': 'idle'}

    def result(self, data):
        item = self.pending.get(data.get('request_id'))
        if not item or item['client_id'] != data.get('client_id') or item['context_key'] != data.get('context_key'):
            raise HTTPException(409, '操作已过期或不属于该扩展/对话')
        if 'progress' in data:
            progress = data['progress']
            if item.get('progress') is None or item['future'].done() or not isinstance(progress, dict) or 'result' in data:
                raise HTTPException(409, '此操作不接受流式增量')
            previous=item['progress'].seq
            item['progress'].push(progress)
            if item.get('trace') and item['progress'].seq>previous and progress.get('kind', 'text') == 'text':
                item['trace'].delta(progress['seq'],prompt_units(progress['delta']))
            return {'ok': True}
        result = data.get('result')
        if not isinstance(result, dict) or len(json.dumps(result)) > 1500000:
            raise HTTPException(400, '对话结果格式或大小无效')
        if not item['future'].done():
            if item.get('trace'):
                item['trace'].event('browser_result',**gateway_log.failure_metadata(result),error_code=result.get('error_code') if isinstance(result.get('error_code'),str) and result['error_code'] in gateway_log.ERROR_CODES else 'OTHER',ok=result.get('ok') is True,completed=result.get('completed') is True,uncertain=result.get('uncertain') is True,chars=prompt_units(result.get('text','')) if isinstance(result.get('text'),str) else 0,**item['trace'].summary())
            item['future'].set_result(result)
        return {'ok': True}

    async def execute(self, kind, *, revision, text='', confirmed=False, progress=None, execution_policy=None, files=None):
        if not self.binding or revision != self.binding['revision']:
            raise HTTPException(409, '绑定已变化，请刷新后重新确认')
        if not self.live():
            raise HTTPException(409, '绑定的浏览器对话离线或已切换；不会改发到其它对话')
        if self.busy:
            raise HTTPException(409, '已有对话操作进行中')
        if kind not in ('read', 'send', 'check'):
            raise HTTPException(400, '不支持的对话操作')
        if kind == 'send':
            if self.uncertain:
                raise HTTPException(409, '上一轮结果未知，请先核对，不会自动重发')
            if confirmed is not True or not isinstance(text, str) or not text.strip() or prompt_units(text) > BOUND_PROMPT_LIMIT:
                raise HTTPException(400, '请确认发送；消息不得超过20万UTF-16字符单位')
        if files and (kind != 'send' or not getattr(self, 'source_pin', None) or not getattr(self,'attachment_capability_provider',lambda:False)()):
            raise HTTPException(400, '附件需要已核验账号的会话路由；未发送')
        self.busy = True
        self.idle.clear()
        b = dict(self.binding)
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = dict(b, future=future, progress=progress, trace=gateway_log.CURRENT.get(), source_pin=getattr(self,'source_pin',None))
        cmd = {'request_id': rid, 'kind': kind, 'context_key': b['context_key'],
               'expires_at': time.time()*1000 + 30000, 'text': text if kind == 'send' else ''}
        if files:
            token=secrets.token_hex(32)
            self.pending[rid].update(attachment_files=files, attachment_token=token)
            cmd.update(attachments=[descriptor(f) for f in files], attachment_token=token)
        if kind == 'send' and execution_policy:
            cmd.update(client_execution=execution_policy.get('client_execution') is True,
                       reference_reads=execution_policy.get('reference_reads') is True,
                       activity_stream=execution_policy.get('activity_stream') is True)
        if progress is not None:
            cmd['stream'] = True
        queued=False;result=None;guard=getattr(self,'persist_send',None)
        try:
            if kind=='send' and guard:guard(True)
            self.queues[b['client_id']].put_nowait(cmd)
            queued=True
            gateway_log.emit('command_enqueued',stream=progress is not None)
            # An accepted send ends on an explicit result or caller disconnect/cancellation, not elapsed time.
            result = await future if kind == 'send' else await asyncio.wait_for(future, 90)
            if kind == 'send' and cmd.get('client_execution') and result.get('ok') is True and result.get('execution_scope_verified') is not True:
                result = {'ok': False, 'completed': False, 'uncertain': True, 'error_code': 'CLIENT_SCOPE_UNVERIFIED'}
            if kind == 'send' and result.get('uncertain') is True:
                # Fail closed: even a lost error response must never invite a blind resend.
                self.uncertain = True
            if progress is not None and result.get('ok') is True:
                final = result.get('text')
                if not isinstance(final, str) or not final.startswith(''.join(progress.parts)):
                    # Set the lock before releasing busy/queue gates, not later in
                    # the HTTP iterator where a waiting request could overtake it.
                    self.uncertain = True
                    raise HTTPException(502, '增量与最终回复不一致；结果待核对，不自动重发')
            if kind == 'check' and result.get('ok') is True and result.get('resolved') is True:
                self.uncertain = False
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            gateway_log.emit('operation_timeout' if isinstance(exc,asyncio.TimeoutError) else 'operation_cancelled')
            if kind == 'send':
                self.uncertain = True
            raise HTTPException(504, '对话操作等待结束；发送结果可能未知，请在原页面核对，不要重发')
        except asyncio.QueueFull:
            raise HTTPException(409, '扩展尚未取走上一次操作，请稍后刷新状态')
        finally:
            try:
                if kind=='send' and guard:
                    known=isinstance(result,dict) and ((result.get('ok') is True and result.get('completed') is True) or result.get('uncertain') is False)
                    if queued and not known:self.uncertain=True
                    guard(self.uncertain)
                elif kind=='check' and guard and isinstance(result,dict) and result.get('ok') is True and result.get('resolved') is True:
                    guard(self.uncertain)
            finally:
                self.pending.pop(rid, None)
                self.busy = False
                self.idle.set()

    async def completion(self, body, request=None):
        if request is None:
            return await self._completion(body)
        stop_watch = asyncio.Event()
        async def disconnected():
            # Starlette's receive CancelScope can consume task.cancel(). Use an
            # explicit stop flag as well, otherwise finally/gather may never exit.
            while not stop_watch.is_set():
                if await request.is_disconnected() or stop_watch.is_set():
                    return
                await asyncio.sleep(0.1)
        work = asyncio.create_task(self._completion(body, request))
        watch = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                response=await work
                gateway_log.emit('request_prepared')
                return response
            raise HTTPException(499, '客户端已断开；未自动重试，请核对已发送请求')
        finally:
            stop_watch.set()
            for task in (work, watch):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, watch, return_exceptions=True)

    async def queued_send(self, prompt, revision, request=None, progress=None, execution_policy=None, files=None):
        gateway_log.emit('queue_enter',waiting=self.waiting)
        if self.waiting >= self.max_waiting:
            raise HTTPException(429, '对话等待队列已满；本请求未发送')
        self.waiting += 1
        counted, acquired = True, False
        async def acquire():
            nonlocal acquired
            await self.completion_gate.acquire()
            acquired = True
            while self.busy:
                await self.idle.wait()
        try:
            try:
                await asyncio.wait_for(acquire(), self.queue_timeout)
            except asyncio.TimeoutError:
                raise HTTPException(429, '对话排队超时；本请求未发送，不会自动重试')
            self.waiting -= 1
            counted = False
            if request is not None and await request.is_disconnected():
                raise HTTPException(499, '客户端已断开；排队请求未发送')
            if not self.binding or self.binding['revision'] != revision or not self.binding['allow_clients']:
                raise HTTPException(409, '排队期间绑定或授权已变化；本请求未发送')
            guard=getattr(self,'request_guard',None)
            if guard:guard()
            # execute rechecks liveness, revision and uncertainty immediately before enqueue.
            extra = {'progress': progress} if progress is not None else {}
            if files: extra['files'] = files
            if execution_policy:
                extra['execution_policy'] = execution_policy
            result = await self.execute('send', revision=revision, text=prompt, confirmed=True, **extra)
            if self.retry_eligible(result, progress, files):
                # Arena 明确拒收（消息没进会话）：等几秒重试一次。只此一次，之后照常报错。
                gateway_log.emit('send_retry', **gateway_log.failure_metadata(result))
                await asyncio.sleep(self.retry_delay)
                if request is not None and await request.is_disconnected():
                    return result
                if not self.binding or self.binding['revision'] != revision or not self.binding['allow_clients'] or self.uncertain:
                    return result
                if guard:guard()
                result = await self.execute('send', revision=revision, text=prompt, confirmed=True, **extra)
                if isinstance(result, dict) and result.get('ok') is not True:
                    result = dict(result, retried=True)
            return result
        finally:
            if counted:
                self.waiting -= 1
            if acquired:
                self.completion_gate.release()

    async def _completion(self, body, request=None):
        body, policy, agent = normalize_request(body)
        if not self.binding or not self.binding['allow_clients']:
            raise HTTPException(403, '请先绑定已有对话，并明确允许客户端向该对话发送')
        revision = self.binding['revision']
        title = local_dsh_title(body, policy)
        if title is not None:
            gateway_log.emit('local_title')
            return self.completion_response(body, {'role': 'assistant', 'content': title}, None)
        audit = None
        removed = []
        client_count = len(policy.tools)
        if policy.tools and not policy.selected:
            # 按模型名白名单删掉不需要的工具定义；被删的工具模型无法调用（parse_calls 会拒绝）
            try:
                kept, removed = getattr(self, 'tool_filter_provider', lambda tools: (tools, []))(policy.tools)
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from None
            if policy.mode == 'required' and not kept:
                raise ProtocolError('tool_choice=required but every declared tool is excluded by the gateway tool allowlist', 'tool_choice')
            policy.tools = kept
        if client_count:
            audit = begin_audit(policy.tools, policy.active)
            audit['client_count'] = client_count
            audit['filtered_names'] = removed
            self.tool_audit_sequence += 1
            audit['sequence'] = self.tool_audit_sequence
            self.last_tool_audit = audit
        try:
            mode = getattr(self, 'send_mode_provider', lambda: 'normal')()
        except (ValueError, OSError):
            raise HTTPException(503, '发送模式配置异常，未发送；请在控制台重新保存发送模式') from None
        if mode not in ('normal', 'full'):
            raise HTTPException(503, '发送模式无效，未发送')
        messages = body['messages'] if mode == 'full' else select_current_messages(body['messages'])
        try:
            files = collect_files(messages)
        except AttachmentError as exc:
            raise ProtocolError(str(exc), 'messages') from None
        if files and (not getattr(self, 'source_pin', None) or not getattr(self,'attachment_capability_provider',lambda:False)()):
            raise ProtocolError('Attachments require an account-verified route and updated attachment-capable extension', 'messages')
        messages=[{k:v for k,v in m.items() if k not in ('_client_files','_client_tool_image_bridge')} for m in messages]
        if mode == 'normal':
            agent = bool(policy.tools or any(m['role'] == 'tool' or m.get('tool_calls') for m in messages))
        reference = False
        if agent:
            reference = policy.active and getattr(self, 'tool_reference_provider', lambda: False)()
            if reference:
                prompt = render_reference_prompt(messages, policy, BOUND_PROMPT_LIMIT, audit=audit)
            else:
                prompt = render_agent_prompt(messages, policy, BOUND_PROMPT_LIMIT, utf16=True, audit=audit)
        elif mode == 'normal':
            prompt = messages[-1]['content']
        else:
            prompt = '\n\n'.join(m['role'] + ': ' + (m.get('content') or '') for m in messages)
        if not prompt.strip():
            raise HTTPException(400, '客户端上下文为空')
        if prompt_units(prompt) > BOUND_PROMPT_LIMIT:
            raise ProtocolError(f'Gateway prompt budget exceeded: total={prompt_units(prompt)}, limit={BOUND_PROMPT_LIMIT} UTF-16 units. No message was submitted; compact client history.', 'messages', 'context_length_exceeded')
        execution_policy = {'client_execution': bool(policy.tools), 'reference_reads': bool(reference),
                            # 网页进度提示（reasoning_content）对所有带工具的请求开启，不再限定模型名或工具文档试验
                            'activity_stream': bool(policy.tools)}
        if body.get('stream'):
            if self.uncertain:
                raise HTTPException(409, '上一轮结果未知，请先核对，不会自动重发')
            if not self.live():
                raise HTTPException(409, '绑定的浏览器对话离线或已切换')
            if self.waiting >= self.max_waiting:
                raise HTTPException(429, '对话等待队列已满；本请求未发送')
            return live_response(self, prompt, revision, policy, agent, execution_policy=execution_policy, **({'files':files} if files else {}))
        result = await self.queued_send(prompt, revision, request, execution_policy=execution_policy, **({'files':files} if files else {}))
        if result.get('ok') is not True or not isinstance(result.get('text'), str) or not result['text'].strip():
            raise completion_failure(result)
        calls = parse_calls(result['text'], policy, 'stop') if agent else None
        message = {'role': 'assistant', 'content': None if calls else result['text']}
        if calls:
            message['tool_calls'] = calls
        return self.completion_response(body, message, calls)

    def completion_response(self, body, message, calls):
        finish = 'tool_calls' if calls else 'stop'
        ident, created = 'chatcmpl-' + uuid.uuid4().hex, int(time.time())
        if not body.get('stream'):
            return {'id': ident, 'object': 'chat.completion', 'created': created, 'model': getattr(self,'model_alias','arena-bound'),
                    'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]}
        async def chunks():
            delta = dict(message)
            if calls:
                delta['tool_calls'] = [dict(x, index=i) for i, x in enumerate(calls)]
            for d, reason in ((delta, None), ({}, finish)):
                yield 'data: ' + json.dumps({'id': ident, 'object': 'chat.completion.chunk', 'created': created,
                    'model': getattr(self,'model_alias','arena-bound'), 'choices': [{'index': 0, 'delta': d, 'finish_reason': reason}]}, ensure_ascii=False) + '\n\n'
            yield 'data: [DONE]\n\n'
        return StreamingResponse(chunks(), media_type='text/event-stream')


def install_conversation_routes(app, verify):
    bridge = ConversationBridge()
    async def body(request):
        raw = await request.body()
        if len(raw) > 1600000:
            raise HTTPException(413, '对话数据过大')
        try:
            data = json.loads(raw)
        except Exception:
            raise HTTPException(400, 'JSON无效')
        if not isinstance(data, dict):
            raise HTTPException(400, '需要JSON对象')
        return data

    @app.post('/v1/conversation/offer')
    async def offer(request: Request):
        data=await body(request)
        router=getattr(bridge,'sources_router',None)
        return router.offer(data) if router and data.get('protocol')==3 else bridge.offer(data)

    @app.get('/v1/conversation/command')
    async def command(client_id: str):
        router=getattr(bridge,'sources_router',None)
        return await router.command(client_id) if router and client_id in router.sources else await bridge.command(client_id)

    @app.get('/v1/conversation/attachment')
    async def attachment(client_id: str, request_id: str, token: str, file_index: int, offset: int):
        # Ephemeral bearer capability is scoped to one live operation and its original source.
        router=getattr(bridge,'sources_router',None)
        item=router.pending(request_id) if router else None
        if not item: raise HTTPException(404, '附件请求已结束或不存在')
        engine, pending=item
        expected=pending.get('attachment_token','')
        source=router.sources.get(client_id,{})
        if (not expected or not re.fullmatch(r'[0-9a-f]{64}',token) or not secrets.compare_digest(expected,token)
            or pending['client_id']!=client_id or pending['future'].done()
            or pending.get('source_pin')!=(source.get('account_key'),source.get('session'))
            or not router.account_enabled(source.get('account_key'))):
            raise HTTPException(403, '附件来源或授权已变化')
        guard=getattr(engine,'request_guard',None)
        if guard: guard()
        files=pending.get('attachment_files',[])
        if file_index<0 or file_index>=len(files) or offset<0 or offset%CHUNK_CHARS:
            raise HTTPException(400,'附件分块索引无效')
        data=files[file_index]['data']
        if offset>len(data):raise HTTPException(400,'附件分块偏移无效')
        chunk=data[offset:offset+CHUNK_CHARS]
        return {'data':chunk,'offset':offset,'next':offset+len(chunk),'done':offset+len(chunk)==len(data)}

    @app.post('/v1/conversation/result')
    async def result(request: Request):
        data=await body(request)
        router=getattr(bridge,'sources_router',None)
        return router.result(data) if router and data.get('protocol')==3 else bridge.result(data)

    @app.get('/v1/conversation/status')
    async def status(request: Request):
        verify(request)
        return bridge.status()

    @app.post('/v1/conversation/bind')
    async def bind(request: Request):
        verify(request)
        return bridge.bind(await body(request))

    @app.post('/v1/conversation/history')
    async def history(request: Request):
        verify(request)
        return await bridge.history(await body(request))

    @app.post('/v1/conversation/connect')
    async def connect(request: Request):
        verify(request)
        return await bridge.connect_history(await body(request))

    @app.post('/v1/conversation/action')
    async def action(request: Request):
        verify(request)
        data = await body(request)
        return await bridge.execute(data.get('kind'), revision=data.get('revision'), text=data.get('text', ''), confirmed=data.get('confirmed') is True)
    return bridge
