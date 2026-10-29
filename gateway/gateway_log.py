"""Allowlisted metadata only. No payloads, exception strings, URLs or account IDs."""
import contextvars
import json
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

CURRENT = contextvars.ContextVar('gateway_trace', default=None)
EVENTS = frozenset(('request_received','route_selected','request_prepared','http_started','http_finished','http_error','client_disconnected',
    'http_send_failed','request_cancelled','queue_enter','command_enqueued','command_taken','command_expired',
    'first_delta','progress_checkpoint','browser_result','operation_timeout','operation_cancelled',
    'stream_started','sse_content','stream_success','stream_error','stream_abandoned','local_title','send_retry'))
FIELDS = frozenset(('status','bytes','chunks','seq','chars','ok','completed','uncertain','waiting','elapsed_ms',
    'first_delta_ms','last_delta_ms','delta_count','delta_chars','error_status','tools','stream','log_seq','upstream_status','failure_stage','submission_attempted','submission_accepted'))
ERROR_CODES = frozenset(['EXECUTION_SCOPE', 'CLIENT_SCOPE_UNVERIFIED', 'STREAM_CONNECT', 'STREAM_READ', 'INTERNAL_ERROR', 'STREAM_TIMEOUT', 'STREAM_ENDED', 'STREAM_PROTOCOL', 'STREAM_LIMIT', 'NETWORK_ERROR', 'MODEL_ERROR', 'ACCESS_DENIED', 'ACCOUNT_CHANGED', 'ACK_PENDING', 'ACK_UNKNOWN', 'AUTH_PROTOCOL', 'BAD_JSON', 'BUSY', 'CANCELLED', 'CONTEXT_CHANGED', 'CONVERSATION_CHANGED', 'NO_SESSION', 'PAGE_BUSY', 'PROTOCOL_CHANGED', 'RATE_LIMIT', 'REPLY_LIMIT', 'RESPONSE_LIMIT', 'SESSION_MISMATCH', 'SNAPSHOT_PROTOCOL', 'TOOL_CONFIRMATION', 'TOOL_STALE', 'UNSUPPORTED_SESSION', 'VERIFICATION_REQUIRED', 'TIMEOUT', 'OTHER'])
# 扩展把上游错误响应归成的固定类别；只接受这些名字，不接受任何原文
UPSTREAM_HINTS = frozenset(('empty','cloudflare_challenge','cloudflare_block','captcha','rate_limit','login','json_error','html_page','other'))
_sink = None

def failure_metadata(result):
    clean={}
    for key in ('submission_attempted','submission_accepted'):
        if type(result.get(key)) is bool:clean[key]=result[key]
    for key,low,high in [('upstream_status',100,599),('failure_stage',1,7)]:
        value=result.get(key)
        if type(value) is int and low<=value<=high:clean[key]=value
    if result.get('upstream_hint') in UPSTREAM_HINTS:clean['upstream_hint']=result['upstream_hint']
    return clean

class EventLog:
    def __init__(self, path, limit=2*1024*1024, backups=3):
        self.path=Path(path);self.limit=limit;self.backups=backups
        self.lock=threading.Lock();self.failures=0
    def write(self, record):
        # Defence in depth: callers cannot accidentally write arbitrary fields/strings.
        if record.get('event') not in EVENTS:return
        trace=record.get('trace','')
        if not isinstance(trace,str) or len(trace)!=32 or any(c not in '0123456789abcdef' for c in trace):return
        clean={'time':time.time(),'trace':trace,'event':record['event']}
        for k,v in record.items():
            if k in FIELDS and type(v) in (bool,int,float) and (type(v) is bool or 0<=v<=10**15):clean[k]=v
        if isinstance(record.get('error_code'),str) and record['error_code'] in ERROR_CODES:clean['error_code']=record['error_code']
        if record.get('upstream_hint') in UPSTREAM_HINTS:clean['upstream_hint']=record['upstream_hint']
        if isinstance(record.get('route_tag'),str) and re.fullmatch('[0-9a-f]{16}',record['route_tag']):clean['route_tag']=record['route_tag']
        raw=(json.dumps(clean,separators=(',',':'))+'\n').encode()
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.exists() and self.path.stat().st_size+len(raw)>self.limit:
                    for i in range(self.backups,0,-1):
                        src=self.path if i==1 else Path(str(self.path)+'.'+str(i-1))
                        dst=Path(str(self.path)+'.'+str(i))
                        if src.exists():src.replace(dst)
                with self.path.open('ab') as f:f.write(raw)
        except OSError:self.failures+=1  # Logging must not trigger a resend or break a reply.

def configure(path):
    global _sink
    _sink=EventLog(path)

def health():return {'enabled':_sink is not None,'write_failures':_sink.failures if _sink else 0}

class Trace:
    def __init__(self):
        self.id=uuid.uuid4().hex;self.start=time.monotonic();self.lock=threading.Lock();self.count=0
        self.delta_count=0;self.delta_chars=0;self.first=None;self.last=None;self.checkpoint=0
    def event(self,event,**fields):
        with self.lock:
            self.count+=1
            if _sink:_sink.write(dict(fields,route_tag=getattr(self,'route_tag',None),event=event,trace=self.id,elapsed_ms=round((time.monotonic()-self.start)*1000),log_seq=self.count))
    def delta(self,seq,chars):
        now=round((time.monotonic()-self.start)*1000)
        self.delta_count+=1;self.delta_chars+=chars;self.last=now
        if self.first is None:self.first=now;self.event('first_delta',seq=seq,chars=chars)
        elif now-self.checkpoint>=5000:self.event('progress_checkpoint',**self.summary());self.checkpoint=now
    def summary(self):
        return dict(delta_count=self.delta_count,delta_chars=self.delta_chars,**({'first_delta_ms':self.first,'last_delta_ms':self.last} if self.first is not None else {}))

def emit(event,**fields):
    t=CURRENT.get()
    if t:t.event(event,**fields)

class TraceMiddleware:
    """Pure ASGI: no request-body reads, response buffering or extra disconnect polling."""
    def __init__(self, app):self.app=app
    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope.get('path')!='/v1/chat/completions':
            return await self.app(scope,receive,send)
        import asyncio
        trace=Trace();token=CURRENT.set(trace);status=0;chunks=0;size=0;finished=False;disconnected=False
        trace.event('request_received')
        async def rx():
            nonlocal disconnected
            msg=await receive()
            if msg['type']=='http.disconnect' and not disconnected and not finished:
                disconnected=True;trace.event('client_disconnected',**trace.summary())
            return msg
        async def tx(msg):
            nonlocal status,chunks,size,finished
            if msg['type']=='http.response.start':
                status=msg['status'];msg=dict(msg,headers=list(msg.get('headers',[]))+[(b'x-arena-trace-id',trace.id.encode())])
            try:await send(msg)
            except OSError:
                trace.event('http_send_failed',status=status,**trace.summary());raise
            if msg['type']=='http.response.start':trace.event('http_started',status=status)
            elif msg['type']=='http.response.body':
                chunks+=1;size+=len(msg.get('body',b''))
                if not msg.get('more_body',False):
                    finished=True;trace.event('http_finished',status=status,chunks=chunks,bytes=size,**trace.summary())
        try:await self.app(scope,rx,tx)
        except asyncio.CancelledError:trace.event('request_cancelled',**trace.summary());raise
        except Exception:trace.event('http_error',status=status,**trace.summary());raise
        finally:
            if not finished and not disconnected:trace.event('stream_abandoned',status=status,**trace.summary())
            CURRENT.reset(token)
