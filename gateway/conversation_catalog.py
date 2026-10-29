"""Account history metadata over the selected instance's existing local channel."""
import asyncio
import time
import uuid
from fastapi import HTTPException


class ConversationCatalog:
    def catalog_sources(self):
        return [dict(o, online=time.time()-o['seen'] < 45) for o in self.offers.values() if o.get('catalog')]

    def catalog_source(self, data):
        source = self.offers.get(data.get('client_id'))
        if not source or not source.get('catalog') or time.time()-source['seen'] >= 45:
            raise HTTPException(409, '历史列表来源离线，请检查实例5与本机桥')
        if source.get('scope_id') != data.get('scope_id'):
            raise HTTPException(409, '浏览器实例来源已变化，停止自动恢复')
        return source

    async def catalog_call(self, source, kind, **fields):
        if self.busy or self.uncertain:
            raise HTTPException(409, '当前操作进行中或结果待核对，不能切换或刷新历史')
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.busy = True
        self.idle.clear()
        self.pending[rid] = dict(source, future=future, progress=None, source_pin=getattr(self,'source_pin',None))
        command = dict(request_id=rid, kind=kind, context_key=source['context_key'],
                       expires_at=time.time()*1000+30000, **fields)
        try:
            self.queues[source['client_id']].put_nowait(command)
            result = await asyncio.wait_for(future, 90)
            if result.get('ok') is not True:
                # Only code-controlled errors, never echo arbitrary browser payloads.
                code = result.get('catalog_error')
                known = {'LOGIN_REQUIRED':'Arena登录或历史读取权限不可用', 'HISTORY_FORMAT':'历史接口格式未识别，已停止',
                         'NOT_IN_HISTORY':'目标不在本次账号历史列表，未连接', 'UNSUPPORTED_CHAT':'此历史条目不是支持的Agent/Work会话',
                         'TAB_AMBIGUOUS':'有多个相同对话标签，未自动选择', 'PAGE_NOT_READY':'对话页面尚未就绪，请等待或刷新该页面',
                         'IDENTITY_UNAVAILABLE':'目标会话页面还没显示账号信息（可能仍在加载或未登录），请稍后重试；未连接',
                         'SOURCE_CHANGED':'实例来源或页面已变化，未连接'}
                raise HTTPException(409, known.get(code, '历史读取或连接未完成；没有发送消息'))
            return result
        except asyncio.TimeoutError:
            raise HTTPException(504, '等待浏览器历史操作超时；没有发送聊天消息')
        except asyncio.QueueFull:
            raise HTTPException(409, '浏览器命令通道繁忙')
        finally:
            self.pending.pop(rid, None)
            self.busy = False
            self.idle.set()

    async def history(self, data):
        source = self.catalog_source(data)
        client = source['client_id']
        cached = self.catalog_cache.setdefault(client, {'rows':{}, 'cursor':None})
        cursor = data.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or len(cursor)>4096 or cursor != cached['cursor']):
            raise HTTPException(400, '分页游标已失效，请重新加载历史')
        result = await self.catalog_call(source, 'catalog_list', cursor=cursor)
        rows = result.get('rows')
        if not isinstance(rows, list) or len(rows)>50:
            raise HTTPException(502, '历史列表格式无效')
        if cursor is None:
            cached['rows'] = {}
        clean=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('context_key'),str):
                raise HTTPException(502,'历史条目格式无效')
            from conversation_bridge import CONTEXT
            if not CONTEXT.fullmatch(row['context_key']):
                continue
            item={'context_key':row['context_key'],'title':str(row.get('title') or '无标题')[:200],
                  'archived':row.get('archived') is True,'updated':str(row.get('updated') or '')[:40]}
            clean.append(item);cached['rows'][item['context_key']]=item
        if len(cached['rows'])>5000:
            raise HTTPException(413, '已达到5000条历史缓存上限，请缩小范围')
        next_cursor=result.get('cursor')
        if next_cursor is not None and (not isinstance(next_cursor,str) or len(next_cursor)>4096 or next_cursor==cursor):
            raise HTTPException(502,'历史分页游标无效')
        cached['cursor']=next_cursor
        return {'rows':clean,'cursor':next_cursor,'has_more':bool(next_cursor),'loaded':len(cached['rows']),
                'source':{'client_id':client,'scope_id':source['scope_id'],'label':source.get('source_label','实例5')}}

    async def connect_history(self, data):
        if data.get('confirmed') is not True:
            raise HTTPException(400,'连接及发送授权需要用户确认')
        source=self.catalog_source(data)
        row=self.catalog_cache.get(source['client_id'],{}).get('rows',{}).get(data.get('context_key'))
        if not row:
            raise HTTPException(409,'请先从当前来源的历史列表选择目标；不会猜测或新建对话')
        result=await self.catalog_call(source,'catalog_select',target=row['context_key'])
        live=self.offers.get(source['client_id'])
        if not live or live.get('scope_id')!=data['scope_id'] or live['context_key']!=row['context_key'] or result.get('context_key')!=row['context_key']:
            raise HTTPException(409,'对话页面未确认到位，客户端发送未授权')
        return self.bind({'client_id':live['client_id'],'context_key':row['context_key'],
                          'confirmed':True,'allow_clients':data.get('allow_clients') is True})
