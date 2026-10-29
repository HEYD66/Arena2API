"""Browser-independent source registry and fixed named conversation routes."""
import asyncio
import copy
import hashlib
import gateway_log
import json
import os
import secrets
import time
from pathlib import Path
from fastapi import HTTPException
from conversation_bridge import ConversationBridge, CONTEXT
from source_identity import clean_source, ALIAS
from tool_reference import trial_eligible

class SourceRouter:
    def __init__(self,path):
        self.review_challenges={}
        self.account_preferences_path=Path(path).with_name('account-preferences.json')
        self.account_preferences={};self.account_preferences_error=False
        if self.account_preferences_path.exists():
            try:
                prefs=json.loads(self.account_preferences_path.read_text(encoding='utf-8'))
                if not isinstance(prefs,dict) or any(not isinstance(k,str) or len(k)!=64 or any(c not in '0123456789abcdef' for c in k) or type(v) is not bool for k,v in prefs.items()):raise ValueError()
                self.account_preferences=prefs
            except (ValueError,OSError):self.account_preferences_error=True
        self.send_mode_provider=lambda: 'normal'
        self.tool_filter=None  # 由 server 注入 ToolFilter；None 表示不过滤
        self.path=Path(path);self.sources={};self.routes={};self.engines={};self.catalogs={};self.queues={};self.lanes={};self.bind_lock=asyncio.Lock()
        if self.path.exists():
            try:
                rows=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(rows,list) or len(rows)>64:raise ValueError()
                for row in rows:
                    if not isinstance(row,dict) or not ALIAS.fullmatch(str(row.get('alias',''))) or not CONTEXT.fullmatch(str(row.get('context_key',''))):raise ValueError()
                    clean_source(dict(row,session='0'*32))
                    if not row.get('account_key') or type(row.get('pending_review',False)) is not bool or type(row.get('tool_reference',False)) is not bool:raise ValueError()
                    self.routes[row['alias']]=dict(row,enabled=False)
            except (ValueError,OSError,TypeError):self.routes.clear();self.load_error=True
    def save(self):
        if getattr(self,'load_error',False):raise HTTPException(409,'绑定配置损坏，未覆盖原文件，请先核对配置')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        rows=[{k:v for k,v in r.items() if k in ('alias','client_id','account_key','context_key','title','allow_clients','account_label','source_label','pending_review','tool_reference')} for r in self.routes.values()]
        temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as f:
            f.write(json.dumps(rows,ensure_ascii=False,indent=2));f.flush();os.fsync(f.fileno())
        temp.replace(self.path)
    def account_enabled(self,key):
        return not self.account_preferences_error and self.account_preferences.get(key,True)
    async def account_control(self,data):
        key=data.get('account_key');enabled=data.get('enabled')
        if data.get('confirmed') is not True or type(enabled) is not bool:raise HTTPException(400,'请明确选择账号启用状态')
        known={s.get('account_key') for s in self.sources.values()}|{r['account_key'] for r in self.routes.values()}
        if not isinstance(key,str) or key not in known or len(key)!=64:raise HTTPException(409,'账号身份未核实，未修改')
        if self.account_preferences_error:raise HTTPException(409,'账号开关配置异常，未覆盖')
        prefs=dict(self.account_preferences);prefs[key]=enabled
        self.account_preferences_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.account_preferences_path.with_suffix('.tmp')
        try:
            with tmp.open('w',encoding='utf-8') as f:json.dump(prefs,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            tmp.replace(self.account_preferences_path)
        except OSError:raise HTTPException(500,'账号开关保存失败，原状态保持不变') from None
        self.account_preferences=prefs
        return self.status()
    def advertised_models(self):
        return [a for a,r in self.routes.items() if r.get('enabled') and self.account_enabled(r['account_key'])]
    def request_guard(self,alias,engine):
        row=self.routes.get(alias)
        if not row or self.engines.get(alias) is not engine or not row.get('enabled'):raise HTTPException(409,'会话已停用，本请求未发送')
        if not self.account_enabled(row['account_key']):raise HTTPException(403,'此账号已停用接入，本请求未发送；不是登录失效')
    def wire_engine(self,alias,b):
        row=self.routes[alias];pin=(row['client_id'],row['account_key'],row['context_key'])
        b.attachment_capability_provider=lambda:self.sources.get(pin[0],{}).get('attachment_protocol')==1
        b.model_alias=alias
        b.tool_reference_provider=lambda:self.reference_enabled(alias,pin)
        b.persist_send=lambda pending:self.persist_send(alias,b,pending)
        b.request_guard=lambda:self.request_guard(alias,b)
        b.tool_filter_provider=lambda tools:self.tool_filter.apply(alias,tools) if self.tool_filter else (tools,[])
    async def rename(self,data):
        alias=data.get('alias');new=data.get('new_alias')
        if data.get('confirmed') is not True or not isinstance(new,str) or not ALIAS.fullmatch(new):raise HTTPException(400,'模型名须以小写字母开头，仅含字母、数字和 . _ -，最多64位')
        async with self.bind_lock:
            row=self.routes.get(alias)
            if not row:raise HTTPException(404,'原模型名不存在')
            if new==alias:return self.status()
            if new in self.routes:raise HTTPException(409,'此模型名已被其他会话使用')
            self.source_idle(row['client_id'])
            old_routes=dict(self.routes);old_engines=dict(self.engines)
            self.routes={new if a==alias else a:r for a,r in self.routes.items()};row['alias']=new
            b=self.engines.pop(alias,None)
            if b:self.engines[new]=b;self.wire_engine(new,b)
            try:self.save()
            except (OSError,HTTPException):
                self.routes=old_routes;self.engines=old_engines;row['alias']=alias
                if b:self.wire_engine(alias,b)
                raise HTTPException(500,'模型名保存失败，已保留原绑定') from None
            return self.status()
    def persist_send(self,alias,engine,pending):
        row=self.routes.get(alias)
        if not row or self.engines.get(alias) is not engine:
            engine.uncertain=True;raise HTTPException(409,'发送绑定已变化，拒绝操作')
        row['pending_review']=bool(pending)
        try:self.save()
        except (OSError,HTTPException):
            row['pending_review']=True;engine.uncertain=True
            raise HTTPException(500,'请求状态保存失败，已锁定此绑定；未自动重发') from None
    def active_source(self,data):
        s=self.sources.get(data.get('client_id'))
        if not s or time.time()-s['seen']>=15:raise HTTPException(409,'扩展来源离线')
        if not s['account_key']:raise HTTPException(409,'账号身份未核验，不能读取历史或发送')
        if s['account_key']!=data.get('account_key'):raise HTTPException(409,'账号已变化，原绑定不会改发到新账号')
        return s
    def offer(self,data):
        s=clean_source(data);s['attachment_protocol']=1 if type(data.get('attachment_protocol')) is int and data['attachment_protocol']==1 else 0
        client=s['client_id'];old=self.sources.get(client)
        if old and old['session']!=s['session'] and time.time()-old['seen']<15:
            raise HTTPException(409,'同一来源出现重叠连接；请等待旧心跳过期或重置复制的扩展来源标识')
        changed=not old or any(old[k]!=s[k] for k in ('session','account_key'))
        if changed:
            for alias,row in self.routes.items():
                if row['client_id']==client:
                    row['enabled']=False
                    if alias in self.engines:self.engines[alias].offers.clear()
            self.catalogs.pop(client,None)
        if client not in self.sources and len(self.sources)>=32:raise HTTPException(409,'来源上限32')
        s['seen']=time.time();self.sources[client]=s
        self.queues.setdefault(client,asyncio.Queue(maxsize=4));self.lanes.setdefault(client,asyncio.Lock())
        if not s['account_key']:return {'ok':True,'protocol':3,'identity_status':'unavailable'}
        c=self.catalogs.setdefault(client,ConversationBridge());c.queues[client]=self.queues[client];c.source_pin=(s['account_key'],s['session'])
        c.offer({'client_id':client,'scope_id':s['account_key'],'catalog':True,'context_key':'https://arena.ai'})
        for alias,row in self.routes.items():
            if row['enabled'] and row['client_id']==client and row['account_key']==s['account_key']:
                b=self.engines[alias];b.offer({'client_id':client,'context_key':row['context_key'],'title':row['title']})
        return {'ok':True,'protocol':3}
    def all_engines(self):return list(self.engines.values())+list(self.catalogs.values())
    def pending(self,rid):
        return next(((b,b.pending[rid]) for b in self.all_engines() if rid in b.pending),None)
    async def command(self,client):
        if client not in self.sources:raise HTTPException(404,'来源未登记')
        try:cmd=await asyncio.wait_for(self.queues[client].get(),20)
        except asyncio.TimeoutError:return {'kind':'idle'}
        item=self.pending(cmd['request_id'])
        if not item or cmd['expires_at']<time.time()*1000:return {'kind':'idle'}
        s=self.sources[client]
        if cmd.get('attachments') and s.get('attachment_protocol')!=1:
            item[0].result(dict(client_id=client,request_id=cmd['request_id'],context_key=cmd['context_key'],result={'ok':False,'uncertain':False,'error_code':'CLIENT_SCOPE_UNVERIFIED'}))
            return {'kind':'idle'}
        if cmd.get('kind')=='send' and not self.account_enabled(s.get('account_key')):
            item[0].result(dict(client_id=client,request_id=cmd['request_id'],context_key=cmd['context_key'],result={'ok':False,'uncertain':False,'error_code':'ACCOUNT_DISABLED','error':'账号已停用；本请求尚未交付浏览器，不会发送'}))
            return {'kind':'idle'}
        # Pending operations pin both account and worker session at enqueue time.
        pin=item[1].get('source_pin')
        if pin and pin!=(s['account_key'],s['session']):
            item[0].result(dict(client_id=client,request_id=cmd['request_id'],context_key=cmd['context_key'],result={'ok':False,'uncertain':False}));return {'kind':'idle'}
        item[1]['source_pin']=(s['account_key'],s['session'])
        if item[1].get('trace'):item[1]['trace'].event('command_taken')
        return dict(cmd,account_key=s['account_key'],source_session=s['session'])
    def result(self,data):
        item=self.pending(data.get('request_id'))
        if not item:raise HTTPException(409,'请求已过期')
        s=self.active_source(data)
        if data.get('session')!=s['session'] or item[1].get('source_pin')!=(s['account_key'],s['session']):raise HTTPException(409,'账号或来源会话已变化')
        return item[0].result(data)
    def status(self):
        return {'sources':[dict(s,account_enabled=self.account_enabled(s.get('account_key')),online=time.time()-s['seen']<15) for s in self.sources.values()],
                'routes':[dict(r,account_enabled=self.account_enabled(r['account_key']),review_available=self.review_available(r),route_tag=hashlib.sha256(a.encode()).hexdigest()[:16],online=r['enabled'] and a in self.engines and self.engines[a].live() and r['client_id'] in self.sources and time.time()-self.sources[r['client_id']]['seen']<15,busy=self.engines[a].busy if a in self.engines else False,uncertain=(self.engines[a].uncertain if a in self.engines else False) or (r.get('pending_review',False) and not (a in self.engines and self.engines[a].busy)),waiting=self.engines[a].waiting if a in self.engines else 0,tool_audit=copy.deepcopy(self.engines[a].last_tool_audit) if a in self.engines else None) for a,r in self.routes.items()],
                'account_controls_supported':True,'account_preferences_error':self.account_preferences_error,'load_error':getattr(self,'load_error',False)}
    def review_available(self,row):
        source=self.sources.get(row['client_id'])
        return bool(row.get('pending_review') and source and source.get('account_key')==row['account_key'] and time.time()-source['seen']<15 and not self.review_busy(row['client_id']))
    def review_busy(self,client):
        return any((b.busy or b.waiting) and (b.binding or {}).get('client_id')==client for b in self.all_engines()) or self.lanes.get(client) is not None and self.lanes[client].locked()
    def review_challenge(self,row):
        # Legacy persisted rows have no upstream input ID. Never infer completion
        # from visible text, an idle page, or a fresh extension's empty memory.
        source=self.active_source(row)
        if not row.get('pending_review'):raise HTTPException(409,'此绑定没有待核对的旧请求')
        if self.review_busy(row['client_id']):raise HTTPException(409,'原来源仍有操作进行中，请等待后核对')
        token=secrets.token_urlsafe(32)
        self.review_challenges[row['alias']]={'token':token,'row':row,'source':(source['account_key'],source['session']),'expires':time.monotonic()+300}
        return {'ok':True,'resolved':False,'review_required':True,'reason':'correlation_unavailable','review_token':token,
                'message':'旧请求的关联信息已丢失，无法自动确认完成。请在原账号、原会话核对本轮已经停止；不会执行网页中的工具指令。'}
    def acknowledge_review(self,row,data):
        source=self.active_source(row);challenge=self.review_challenges.get(row['alias'])
        token=data.get('review_token')
        if data.get('acknowledged') is not True or not isinstance(token,str) or len(token)>128 or not challenge or not secrets.compare_digest(token,challenge['token']):raise HTTPException(409,'须先核对旧请求并显式确认；未改变绑定')
        if challenge['row'] is not row or challenge['expires']<time.monotonic() or challenge['source']!=(source['account_key'],source['session']):raise HTTPException(409,'核对期间来源或绑定已变化，请重新核对')
        if not row.get('pending_review') or self.review_busy(row['client_id']):raise HTTPException(409,'请求状态已变化或原来源仍有操作，未结束待核对状态')
        previous=dict(row);row['pending_review']=False;row['enabled']=False
        try:self.save()
        except (OSError,HTTPException):
            row.clear();row.update(previous)
            raise HTTPException(500,'人工核对记录保存失败，原保护状态保持不变') from None
        self.engines.pop(row['alias'],None);self.review_challenges.pop(row['alias'],None)
        return {'ok':True,'resolved':False,'review_acknowledged':True,'enabled':False,'replayed':False}
    def source_idle(self,client):
        if any(r['client_id']==client and r.get('pending_review',False) for r in self.routes.values()):raise HTTPException(409,'该来源有未确认结束的请求记录；重启不会解除结果未知锁，须核对原请求，禁止自动恢复或重发')
        if any((b.busy or b.uncertain or b.waiting) and b.binding and b.binding['client_id']==client for b in self.engines.values()):raise HTTPException(409,'该来源仍有请求或结果未知，先核对再修改绑定')
    async def history(self,data):
        s=self.active_source(data);self.source_idle(s['client_id'])
        return await self.catalogs[s['client_id']].history({'client_id':s['client_id'],'scope_id':s['account_key'],'cursor':data.get('cursor')})
    async def bind(self,data):
        async with self.bind_lock:return await self._bind(data)
    async def _bind(self,data):
        if getattr(self,'load_error',False):raise HTTPException(409,'绑定配置损坏，未覆盖或启用新绑定')
        alias=data.get('alias','')
        if not isinstance(alias,str) or not ALIAS.fullmatch(alias):raise HTTPException(400,'模型别名用小写字母开头，最多64位，仅字母数字和._-')
        if data.get('confirmed') is not True or data.get('allow_clients') is not True:raise HTTPException(400,'请选择已有会话并明确授权发送')
        if len(self.routes)>=64 and alias not in self.routes:raise HTTPException(409,'绑定上限64')
        s=self.active_source(data);client=s['client_id'];self.source_idle(client)
        if alias in self.routes:
            old=self.routes[alias]
            if (old['client_id'],old['account_key'],old['context_key'])!=(client,s['account_key'],data.get('context_key')):raise HTTPException(409,'别名已占用；请显式删除旧绑定后再使用，不静默改发目标')
        # Prevent separate aliases bypassing the same physical conversation's uncertainty lock.
        if any(a!=alias and (r['account_key'],r['context_key'])==(s['account_key'],data.get('context_key')) for a,r in self.routes.items()):raise HTTPException(409,'该来源的此会话已有别名，请使用原别名')
        c=self.catalogs[client];row=c.catalog_cache.get(client,{}).get('rows',{}).get(data.get('context_key'))
        if not row:raise HTTPException(409,'请先从当前账号历史列表选择会话')
        session=s['session'];result=await c.catalog_call(c.offers[client],'catalog_select',target=row['context_key'])
        now=self.active_source(data)
        if now['session']!=session or result.get('context_key')!=row['context_key']:raise HTTPException(409,'选择期间来源已变化，未授权')
        self.source_idle(client)  # A client may have started a request during browser verification.
        b=ConversationBridge();b.model_alias=alias;b.completion_gate=self.lanes[client];b.queues[client]=self.queues[client]
        b.offer({'client_id':client,'context_key':row['context_key'],'title':row['title']});b.bind({'client_id':client,'context_key':row['context_key'],'confirmed':True,'allow_clients':True})
        b.source_pin=(s['account_key'],session)
        b.send_mode_provider=lambda:self.send_mode_provider()
        pin=(client,s['account_key'],row['context_key'])
        b.tool_reference_provider=lambda:self.reference_enabled(alias,pin)
        b.persist_send=lambda pending:self.persist_send(alias,b,pending)
        previous=self.routes.get(alias);previous_engine=self.engines.get(alias)
        self.engines[alias]=b;self.routes[alias]=dict(alias=alias,client_id=client,account_key=s['account_key'],context_key=row['context_key'],title=row['title'],account_label=s['label'],source_label=s['source_label'],allow_clients=True,enabled=True,tool_reference=bool(previous and previous.get('tool_reference',False)))
        try:self.save()
        except (OSError,HTTPException):
            if previous:self.routes[alias]=previous
            else:self.routes.pop(alias,None)
            if previous_engine:self.engines[alias]=previous_engine
            else:self.engines.pop(alias,None)
            raise HTTPException(500,'绑定保存失败，未启用新授权') from None
        self.wire_engine(alias,b)
        return self.status()
    async def restore(self,data):
        row=self.routes.get(data.get('alias'))
        if not row:raise HTTPException(404,'绑定不存在')
        if data.get('confirmed') is not True:raise HTTPException(400,'恢复绑定需要确认')
        source=self.active_source(row);self.source_idle(source['client_id']);cursor=None
        for _ in range(50):
            page=await self.history(dict(row,cursor=cursor))
            if any(r['context_key']==row['context_key'] for r in page['rows']):return await self.bind(dict(row,confirmed=True,allow_clients=True))
            cursor=page.get('cursor')
            if not cursor:break
        raise HTTPException(409,'未在本次最多2500条历史中找到原会话，未换目标')
    def reference_enabled(self,alias,pin):
        row=self.routes.get(alias,{})
        return trial_eligible(row) and row.get('tool_reference') is True and pin==(row.get('client_id'),row.get('account_key'),row.get('context_key'))
    async def control(self,data):
        row=self.routes.get(data.get('alias'))
        if not row:raise HTTPException(404,'绑定不存在')
        if data.get('confirmed') is not True:raise HTTPException(400,'操作需要确认')
        if getattr(self,'load_error',False):raise HTTPException(409,'绑定配置异常，未修改旧请求状态')
        action=data.get('action');b=self.engines.get(row['alias'])
        if action=='acknowledge_review':return self.acknowledge_review(row,data)
        if action in ('reference_on','reference_off'):
            if action=='reference_on' and not trial_eligible(row):raise HTTPException(403,'本次试验仅限实例5的 opus5.5 原绑定')
            self.source_idle(row['client_id'])
            previous=dict(row);row['tool_reference']=action=='reference_on'
            try:self.save()
            except (OSError,HTTPException):
                row.clear();row.update(previous)
                raise HTTPException(500,'工具文档试验设置保存失败；原设置未改变') from None
            return self.status()
        if action=='check':
            if not b or not b.binding or not b.live() or not row.get('enabled'):return self.review_challenge(row)
            result=await b.execute('check',revision=b.binding['revision'])
            # 扩展刷新/重载后丢失本轮记录时自动核对永远无法解除；转人工核对（确认后绑定停用，需另行启用，不重发）
            if isinstance(result,dict) and result.get('resolved') is not True and row.get('pending_review'):return self.review_challenge(row)
            return result
        if action not in ('disable','delete'):raise HTTPException(400,'不支持的操作')
        self.source_idle(row['client_id']);row['enabled']=False
        if b:b.binding=None
        if action=='delete':self.routes.pop(row['alias']);self.engines.pop(row['alias'],None)
        self.save();return self.status()
    async def completion(self,body,request):
        trace=gateway_log.CURRENT.get()
        if trace:
            trace.route_tag=hashlib.sha256(body['model'].encode()).hexdigest()[:16];trace.event('route_selected')
        alias=body['model'];row=self.routes.get(alias);b=self.engines.get(alias)
        if not row or not b or not row['enabled']:raise HTTPException(409,'此模型别名尚未恢复或已停用，请在控制台确认连接')
        self.request_guard(alias,b)
        s=self.active_source(row)
        if b.source_pin!=(s['account_key'],s['session']):raise HTTPException(409,'来源已重启或账号变化，请重新核验绑定')
        return await b.completion(body,request=request)
