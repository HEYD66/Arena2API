"""Conversation-only desktop controller. One-click starts only this project's processes."""
import json
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from send_settings import SendModeSettings

DEFAULTS={'port':9090,'api_key':'arena-local'}
KEY=re.compile(r'^https://arena\.ai/(agent|work)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

class ConsoleCore:
    def __init__(self,root):
        self.root=Path(root).resolve();self.config_file=self.root/'.console/config.json';self.preference_file=self.root/'.console/connection.json'
        self.send_settings=SendModeSettings(self.root/'.console/send-mode.json')
        self.config=dict(DEFAULTS);self.config_warning='';self.saved=None
        try:
            if self.config_file.exists():self.config=self.validate_config(json.loads(self.config_file.read_text(encoding='utf-8')))
        except (ValueError,OSError,TypeError):self.config_warning='配置无法读取，未覆盖原文件。'
        try:
            if self.preference_file.exists():
                p=json.loads(self.preference_file.read_text(encoding='utf-8'))
                if KEY.fullmatch(p.get('context_key','')) and re.fullmatch('[0-9a-f]{64}',p.get('scope_id','')):self.saved=p
        except (ValueError,OSError,TypeError):pass
        self.bridge_preferences_file=self.root/'.console/bridges.json';self.saved_bridges=[];self.bridge_config_error=False
        try:
            if self.bridge_preferences_file.exists():
                rows=json.loads(self.bridge_preferences_file.read_text(encoding='utf-8'))
                if not isinstance(rows,list) or len(rows)>32:raise ValueError()
                parsed=[self.validate_bridge(row) for row in rows]
                if len({self.bridge_key(row) for row in parsed})!=len(parsed):raise ValueError()
                self.saved_bridges=parsed
        except (ValueError,OSError,TypeError):
            self.bridge_config_error=True;self.config_warning+=' 兼容桥配置无法读取；未覆盖或自动启动，请检查项目内配置。'
        self.proc=None;self.bridge_proc=None;self.compat_bridges={};self.lock=threading.RLock();self.test_lock=threading.Lock()
        self.logs=deque(maxlen=200);self.sequence=0;self.closing=False;self.phase={'step':'idle','message':'点击一次即可准备连接'}
        self.add_log('info','工作台就绪；未自动读取历史或发送消息。')

    @staticmethod
    def validate_config(data):
        if not isinstance(data,dict):raise ValueError('配置格式错误')
        port=data.get('port',9090)
        if isinstance(port,(bool,float)):raise ValueError('端口无效')
        try:port=int(port)
        except (ValueError,TypeError):raise ValueError('端口必须为整数')
        key=data.get('api_key','')
        if not 1024<=port<=65535:raise ValueError('端口范围1024–65535')
        if not isinstance(key,str) or not key or len(key)>512 or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('本地API Key无效')
        return {'port':port,'api_key':key}

    def config_snapshot(self):
        with self.lock:return dict(self.config)
    def add_log(self,level,message):
        with self.lock:
            self.sequence+=1;self.logs.append({'id':self.sequence,'time':time.strftime('%H:%M:%S'),'level':level,'message':message})
    def _phase(self,step,message):
        with self.lock:self.phase={'step':step,'message':message}
    def boot(self):
        return {'config':self.config_snapshot(),'base_url':f'http://127.0.0.1:{self.config["port"]}/v1','model':'arena-bound',
                'send_settings':self.send_settings.status(),'saved_bridges':[dict(r) for r in self.saved_bridges],'saved':self.saved,'warning':self.config_warning,'version':'0.5.0','extension_path':str(self.root/'extension/chrome')}
    def _save_json(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
    def save_config(self,data):
        cfg=self.validate_config(data)
        with self.lock:
            if self.test_lock.locked():raise ValueError('操作进行中，不能修改配置')
            if self.proc and self.proc.poll() is None:raise ValueError('先停止本窗口网关，再修改连接设置')
            if cfg!=self.config:
                for proc in self.compat_bridges.values():
                    if proc.poll() is None:self._terminate(proc)
                self.compat_bridges.clear()
            if cfg!=self.config and self.bridge_proc and self.bridge_proc.poll() is None:
                self._terminate(self.bridge_proc);self.bridge_proc=None
            self._save_json(self.config_file,cfg);self.config=cfg
        self.add_log('info','连接设置已保存；未修改DSH。');return self.boot()

    def save_send_mode(self,data):
        if not isinstance(data,dict) or data.get('confirmed') is not True:raise ValueError('请明确保存发送模式')
        def work():
            result=self.send_settings.save(data.get('mode'))
            self.add_log('info','发送模式已保存，仅作用于后续进入网关的请求；没有发送消息。')
            return result
        return self._operation(work)

    def _http(self,method,path,data=None,timeout=4,cfg=None):
        if path not in ('/health','/v1/models','/v1/conversation/status','/v1/conversation/bind','/v1/conversation/action','/v1/conversation/history','/v1/conversation/connect','/v1/workspace/status','/v1/workspace/history','/v1/workspace/bind','/v1/workspace/restore','/v1/workspace/control'):
            raise ValueError('不支持的控制台请求')
        cfg=cfg or self.config_snapshot();raw=None if data is None else json.dumps(data,ensure_ascii=False).encode()
        req=urllib.request.Request(f'http://127.0.0.1:{cfg["port"]}{path}',data=raw,method=method,headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['api_key']})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            with opener.open(req,timeout=timeout) as response:
                value=response.read(2_000_001)
                if len(value)>2_000_000:raise ValueError('本地响应过大')
                return json.loads(value.decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:message=json.loads(e.read(8192).decode('utf-8')).get('error',{}).get('message')
            except Exception:message=None
            raise ValueError(str(message or '本地服务返回错误，请检查连接与授权')[:500]) from None

    def status(self):
        owned=self.proc is not None and self.proc.poll() is None
        out={'online':False,'compatible':False,'owned':owned,'busy':self.test_lock.locked(),'phase':dict(self.phase),'bridge_owned':self.bridge_proc is not None and self.bridge_proc.poll() is None}
        try:
            h=self._http('GET','/health');out.update(online=h.get('status')=='ok',compatible=h.get('product')=='arena2api-conversations',version=h.get('version',''),logging=h.get('logging',{}),account_controls_supported=h.get('account_controls_supported') is True,tool_reference_supported=h.get('tool_reference_supported') is True,send_mode_supported=h.get('send_mode_supported') is True,send_mode=h.get('send_mode'),send_mode_error=h.get('send_mode_error') is True)
        except Exception:pass
        out['bridge_processes']=[{'name':r['name'],'cdp_port':r['cdp_port'],'exited': bool((p:=self.compat_bridges.get((*self.bridge_key(r),self.config['port']))) is not None and p.poll() is not None)} for r in self.saved_bridges]
        return out
    def _capture(self,p,label):
        try:
            for line in p.stdout:
                if 'Application startup complete' in line:self.add_log('info','网关已启动。')
                elif 'address already in use' in line.lower():self.add_log('error','端口被占用，未接管其它进程。')
                elif label=='bridge' and line.startswith('READY:'):self.add_log('info','目标浏览器本机桥已连接。')
                elif label=='bridge' and line.startswith('Bridge waiting:'):self._phase('bridge','兼容桥等待指定目标；检查Arena页面及重复目标标签')
            code=p.wait()
            if label=='bridge' and code:
                self.add_log('error','兼容桥已退出；请检查Node.js版本、目标和端口，然后点击一键启动重试。')
                self._phase('bridge_error','兼容桥已退出，未就绪；未自动替换来源，请检查连接设置与活动日志')
        finally:
            if p.stdout:p.stdout.close()
    def _spawn(self,args,env=None):
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        return subprocess.Popen(args,cwd=self.root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=flags)
    def start(self):
        with self.lock:
            if self.closing:raise ValueError('窗口正在关闭')
            if self.proc and self.proc.poll() is None:return {'ok':True}
            cfg=dict(self.config)
            with socket.socket() as sock:
                sock.settimeout(.5)
                if sock.connect_ex(('127.0.0.1',cfg['port']))==0:
                    status=self.status()
                    if not status['compatible']:raise ValueError('端口仍被旧网关或其它程序占用，请手动停止它；不会接管外部进程')
                    return {'ok':True,'external':True}
            python=self.root/'runtime/python/python.exe'
            if not python.is_file():python=self.root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            if not python.exists():raise ValueError('缺少项目Python环境，请使用启动控制台.cmd')
            env=dict(os.environ);env.update(API_KEY=cfg['api_key'],PORT=str(cfg['port']),PYTHONIOENCODING='utf-8')
            self.proc=self._spawn([str(python),'-X','utf8','-B','-m','uvicorn','server:app','--host','127.0.0.1','--port',str(cfg['port']),'--no-access-log'],env)
            threading.Thread(target=self._capture,args=(self.proc,'gateway'),daemon=True).start()
        return {'ok':True}
    @staticmethod
    def _pid_running(pid):
        # Query-only: os.kill(pid, 0) must never be used on Windows.
        if type(pid) is not int or pid<=0:return None
        if os.name!='nt':
            return Path('/proc',str(pid)).exists() if Path('/proc').is_dir() else None
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return False if ctypes.get_last_error()==87 else None
        try:
            code=ctypes.c_ulong()
            return code.value==259 if kernel.GetExitCodeProcess(handle,ctypes.byref(code)) else None
        finally:kernel.CloseHandle(handle)

    def _bridge(self):
        if self.bridge_proc and self.bridge_proc.poll() is None:return
        status=self.root/'.console/local-cdp-bridge-status.json'
        lockfile=self.root/'.console/local-cdp-bridge.lock'
        try:v=json.loads(status.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):v={}
        if not isinstance(v,dict):v={}
        if lockfile.exists():
            try:lock=json.loads(lockfile.read_text(encoding='utf-8'));pid=lock.get('pid')
            except (OSError,ValueError,AttributeError):raise ValueError('本机桥锁记录无法核验；未清锁或启动第二个桥，请检查旧本机桥窗口') from None
            alive=self._pid_running(pid)
            if alive is None:raise ValueError('无法只读核验现有本机桥进程；未清锁或接管，请检查旧本机桥窗口')
            if alive:
                if v.get('pid')!=pid:raise ValueError('本机桥锁与状态不一致；请检查旧本机桥窗口，未自动清锁')
                if v.get('protocol')!=2:raise ValueError('旧版本本机对话桥仍在运行，阻止新版接入。请关闭之前“启动本机对话桥.cmd”的桥接窗口（不要关闭千面），再点击连接；不需要重新登录')
                if v.get('gateway_port')!=self.config['port']:raise ValueError('现有本机桥仍连接旧端口；请关闭该桥接窗口，再点击连接')
                stamp=v.get('updated_at')
                age=time.time()-stamp/1000 if isinstance(stamp,(int,float)) else float('inf')
                if not v.get('ready') or not 0<=age<12:raise ValueError('本机桥进程仍在运行，但尚未就绪或心跳已失效；请查看桥接窗口报错。未启动第二个桥，也未判断为登录失败')
                return
        node=str(self.root/'runtime/node/node.exe') if (self.root/'runtime/node/node.exe').is_file() else shutil.which('node')
        if not node:raise ValueError('未找到Node.js 22+；不会自动安装或改PATH')
        self.bridge_proc=self._spawn([node,str(self.root/'desktop/local_cdp_bridge.cjs'),'--instance','实例 5','--cdp','36938','--gateway',str(self.config['port'])])
        threading.Thread(target=self._capture,args=(self.bridge_proc,'bridge'),daemon=True).start()
    def _prepare(self):
        self._phase('gateway','启动或检查本地网关');self.start()
        until=time.monotonic()+12
        while time.monotonic()<until:
            if self.status().get('compatible'):break
            time.sleep(.2)
        else:raise ValueError('网关未在预期时间内就绪')
        self._phase('bridge','连接实例5的项目专用本机桥');self._bridge()
        until=time.monotonic()+20
        while time.monotonic()<until:
            if self.closing:raise ValueError('窗口正在关闭')
            if self.bridge_proc and self.bridge_proc.poll() is not None:raise ValueError('本机桥进程已退出，未进入历史读取。请检查旧桥窗口是否仍占用通道，以及Node.js版本')
            s=self._http('GET','/v1/conversation/status')
            sources=[o for o in s.get('sources',[]) if o.get('online')]
            if len(sources)>1:raise ValueError('出现多个来源，停止自动选择')
            if len(sources)==1:return sources[0]
            time.sleep(.5)
        raise ValueError('没有收到实例5的历史来源。确认新版扩展已加载、Arena已登录；来源不明确时不会自动选择')
    def _operation(self,work):
        if self.closing:raise ValueError('窗口正在关闭')
        if not self.test_lock.acquire(False):raise ValueError('已有连接操作进行中')
        try:return work()
        except Exception:
            self._phase('error','操作未完成；未自动换对话或发送消息');raise
        finally:self.test_lock.release()
    def history(self,data=None):
        data=data or {}
        def work():
            source=self._prepare();self._phase('history','读取当前账号的历史标题列表')
            result=self._http('POST','/v1/conversation/history',{'client_id':source['client_id'],'scope_id':source['scope_id'],'cursor':data.get('cursor')},timeout=100)
            self._phase('select','选择一个历史对话，连接不会发送消息');return result
        return self._operation(work)
    def connect(self,data=None):
        data=data or {}
        def work():
            if data.get('selection') and (data.get('confirmed') is not True or data.get('allow_clients') is not True):raise ValueError('请选择并确认连接及发送授权')
            source=self._prepare();selection=data.get('selection') or self.saved
            current=self._http('GET','/v1/conversation/status')
            if current.get('binding'):
                self._http('POST','/v1/conversation/bind',{'confirmed':True,'unbind':True})
            if not selection:
                self._phase('history','首次连接：加载历史供你选择')
                history=self._http('POST','/v1/conversation/history',{'client_id':source['client_id'],'scope_id':source['scope_id']},timeout=100)
                self._phase('select','选择对话并确认一次发送授权');return {'needs_selection':True,'history':history}
            if selection.get('scope_id')!=source['scope_id'] or not KEY.fullmatch(selection.get('context_key','')):
                raise ValueError('上次选择与当前实例来源不一致，请手动重新选择，不自动换目标')
            if data.get('selection') and data.get('confirmed') is not True:raise ValueError('请选择并确认连接及发送授权')
            self._phase('history','核对当前历史中的目标，仅向工作台返回元数据')
            cursor=None;found=selection if data.get('selection') else None
            for _ in range(0 if found else 50):
                page=self._http('POST','/v1/conversation/history',{'client_id':source['client_id'],'scope_id':source['scope_id'],'cursor':cursor},timeout=100)
                found=next((r for r in page['rows'] if r['context_key']==selection['context_key']),None)
                if found or not page.get('cursor'):break
                cursor=page['cursor']
            if not found:raise ValueError('在本次最多2500条历史核验范围内未找到上次对话，请重新选择；不会新建或替换')
            allow=(data.get('allow_clients') is True) if data.get('selection') else selection.get('allow_clients') is True
            if not allow:raise ValueError('连接DSH需要你明确允许客户端发送；请勾选后连接')
            self._phase('binding','打开或定位所选已有对话并确认绑定')
            result=self._http('POST','/v1/conversation/connect',{'client_id':source['client_id'],'scope_id':source['scope_id'],'context_key':found['context_key'],'confirmed':True,'allow_clients':True},timeout=100)
            self.saved={'scope_id':source['scope_id'],'context_key':found['context_key'],'title':found['title'],'allow_clients':True}
            self._save_json(self.preference_file,self.saved)
            self._phase('ready','已连接，DSH可直接使用arena-bound');self.add_log('info','目标核验及绑定完成，未发送测试消息。')
            return {'ready':True,'saved':self.saved,'status':result}
        return self._operation(work)
    def conversation(self,action,data=None):
        data=data or {}
        if action=='status':return self._http('GET','/v1/conversation/status')
        if action not in ('bind','read','check','send'):raise ValueError('不支持的对话操作')
        if action in ('bind','send') and data.get('confirmed') is not True:raise ValueError('需要确认')
        def work():
            payload=dict(data)
            if action!='bind':payload['kind']=action
            return self._http('POST','/v1/conversation/bind' if action=='bind' else '/v1/conversation/action',payload,timeout=None if action=='send' else 100)
        return self._operation(work)
    def stop(self,confirmed=False):
        if confirmed is not True:raise ValueError('需要确认停止')
        if self.test_lock.locked():raise ValueError('连接操作进行中，不能停止')
        try:
            s=self._http('GET','/v1/conversation/status')
            if s.get('busy') or s.get('uncertain'):raise ValueError('对话操作进行中或结果未知，先停止客户端并核对原会话')
        except (urllib.error.URLError,TimeoutError):pass
        try:
            routes=self.workspace_status().get('routes',[])
            if any(r.get('busy') or r.get('uncertain') or r.get('waiting') for r in routes):raise ValueError('仍有模型别名请求进行中或结果未知，请先核对')
        except (urllib.error.URLError,TimeoutError):pass
        if self.proc is None or self.proc.poll() is not None:return {'ok':False,'message':'外部网关不会被本窗口停止'}
        self._terminate(self.proc);self._phase('idle','网关已停止');return {'ok':True}
    @staticmethod
    def _terminate(p):
        p.terminate()
        try:p.wait(timeout=6)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
    def workspace_status(self):
        return self._http('GET','/v1/workspace/status')
    def workspace_start(self):
        def work():
            self.start();until=time.monotonic()+12
            while time.monotonic()<until:
                current=self.status()
                if current.get('compatible'):
                    if current.get('version')!='0.5.0':raise ValueError('此端口仍运行旧网关，请关闭旧网关后重试；不会接管外部进程')
                    if current.get('send_mode_supported') is not True:raise ValueError('当前运行的网关不支持发送模式；请先停止旧网关再启动新版，不会自动重启或恢复绑定')
                    errors=[]
                    if self.bridge_config_error:errors.append({'name':'兼容桥配置','message':'配置损坏，未启动任何保存的桥；请检查项目内配置'})
                    for target in self.saved_bridges:
                        try:self._ensure_compat_bridge(target)
                        except (ValueError,OSError):
                            errors.append({'name':target['name'],'message':'桥未就绪或无法启动；请检查Node.js、已有桥状态和目标端口。未启动重复桥或替换目标'})
                    self._phase('ready','网关就绪，已处理保存的兼容桥；等待扩展登记后核验原绑定')
                    return dict(self.workspace_status(),bridge_errors=errors)
                time.sleep(.2)
            raise ValueError('网关尚未就绪；未读取账号或发送消息')
        return self._operation(work)
    def workspace_action(self,action,data=None):
        if action not in ('history','bind','restore','control','account','rename'):raise ValueError('不支持的绑定操作')
        return self._operation(lambda:self._http('POST','/v1/workspace/'+action,data or {},timeout=310 if action=='restore' else 100))
    @staticmethod
    def validate_bridge(data):
        if not isinstance(data,dict):raise ValueError('兼容桥配置格式错误')
        name=data.get('name');port=data.get('cdp_port')
        if not isinstance(name,str) or not name.strip() or len(name)>80 or any(ord(c)<32 for c in name) or type(port) is not int or not 1024<=port<=65535:
            raise ValueError('请填写目标实例名称和有效CDP端口')
        return {'name':name.strip(),'cdp_port':port}
    @staticmethod
    def bridge_key(target):return (re.sub(r'\s','',target['name']),target['cdp_port'])
    def _ensure_compat_bridge(self,target):
        name,cdp=self.bridge_key(target);key=(name,cdp,self.config['port'])
        old=self.compat_bridges.get(key)
        if old and old.poll() is None:return {'state':'running','owned':True}
        suffix='' if name=='实例5' else '-'+hashlib.sha256((name+'|'+str(cdp)).encode()).hexdigest()[:12]
        base=self.root/'.console'/('local-cdp-bridge'+suffix)
        lockfile=Path(str(base)+'.lock');statusfile=Path(str(base)+'-status.json')
        if lockfile.exists():
            try:
                lock=json.loads(lockfile.read_text(encoding='utf-8'));pid=lock.get('pid')
                alive=self._pid_running(pid)
                if alive is None:raise ValueError()
                if alive:
                    info=json.loads(statusfile.read_text(encoding='utf-8'))
                    if not isinstance(info,dict):raise ValueError()
                    stamp=info.get('updated_at');age=time.time()-stamp/1000 if type(stamp) in (int,float) else float('inf')
                    if info.get('pid')!=pid or info.get('protocol')!=2 or info.get('gateway_port')!=self.config['port'] or re.sub(r'\s','',str(info.get('instance','')))!=name or info.get('cdp_port',36938 if name=='实例5' else cdp)!=cdp or not info.get('ready') or not 0<=age<12:raise ValueError()
                    return {'state':'running','owned':False}
            except (OSError,ValueError,TypeError,AttributeError):
                raise ValueError('已有桥的锁或状态无法核验；请检查原桥窗口与端口，未清锁、接管或启动第二个桥') from None
        node=str(self.root/'runtime/node/node.exe') if (self.root/'runtime/node/node.exe').is_file() else shutil.which('node')
        if not node:raise ValueError('缺少Node.js 22+，不会自动安装')
        proc=self._spawn([node,str(self.root/'desktop/local_cdp_bridge.cjs'),'--instance',target['name'],'--cdp',str(cdp),'--gateway',str(self.config['port'])])
        self.compat_bridges[key]=proc
        threading.Thread(target=self._capture,args=(proc,'bridge'),daemon=True).start()
        return {'state':'starting','owned':True}
    def compat_bridge(self,data):
        target=self.validate_bridge(data)
        if data.get('confirmed') is not True:raise ValueError('请明确授权此兼容桥目标；不会扫描其他实例')
        action=data.get('action','save_start')
        if action not in ('save_start','remove'):raise ValueError('不支持的兼容桥操作')
        def work():
            if self.bridge_config_error:raise ValueError('兼容桥配置损坏，未覆盖原文件，请先检查配置')
            key=self.bridge_key(target);rows=[r for r in self.saved_bridges if self.bridge_key(r)!=key]
            if action=='save_start':rows.append(target)
            if len(rows)>32:raise ValueError('兼容桥配置上限32')
            self._save_json(self.bridge_preferences_file,rows);self.saved_bridges=rows
            if action=='remove':
                return {'ok':True,'saved_bridges':rows,'message':'已取消随启动；未停止正在运行的桥，避免中断请求'}
            self.start();result=self._ensure_compat_bridge(target)
            self.add_log('info','已保存明确授权的兼容桥；以后点击一键启动时复用或启动，实际就绪以来源状态为准。')
            return dict(result,ok=True,saved_bridges=rows)
        return self._operation(work)

    def gateway_logs(self):
        path=self.root/'logs/gateway-events.jsonl'
        if not path.exists():return {'available':False,'events':[],'message':'尚无链路日志；需重启新版网关后才会记录新请求，不能补回旧请求。'}
        try:
            with path.open('rb') as f:
                f.seek(0,2);size=f.tell();start=max(0,size-262144);f.seek(start)
                if start:f.readline()
                lines=f.read(262144).decode('utf-8',errors='replace').splitlines()[-150:]
            events=[]
            allowed={'request_received','route_selected','request_prepared','http_started','http_finished','http_error','client_disconnected','http_send_failed','request_cancelled','queue_enter','command_enqueued','command_taken','command_expired','first_delta','progress_checkpoint','browser_result','operation_timeout','operation_cancelled','stream_started','sse_content','stream_success','stream_error','stream_abandoned','local_title'}
            fields={'time','status','bytes','chunks','seq','chars','ok','completed','uncertain','waiting','elapsed_ms','first_delta_ms','last_delta_ms','delta_count','delta_chars','error_status','tools','stream','log_seq','upstream_status','failure_stage','submission_attempted','submission_accepted'}
            for line in lines:
                try:v=json.loads(line)
                except ValueError:continue
                if not isinstance(v,dict) or v.get('event') not in allowed or not re.fullmatch('[0-9a-f]{32}',str(v.get('trace',''))):continue
                if v.get('error_code') not in ['STREAM_TIMEOUT', 'STREAM_ENDED', 'STREAM_PROTOCOL', 'STREAM_LIMIT', 'NETWORK_ERROR', 'MODEL_ERROR', 'ACCESS_DENIED', 'ACCOUNT_CHANGED', 'ACK_PENDING', 'ACK_UNKNOWN', 'AUTH_PROTOCOL', 'BAD_JSON', 'BUSY', 'CANCELLED', 'CONTEXT_CHANGED', 'CONVERSATION_CHANGED', 'NO_SESSION', 'PAGE_BUSY', 'PROTOCOL_CHANGED', 'RATE_LIMIT', 'REPLY_LIMIT', 'RESPONSE_LIMIT', 'SESSION_MISMATCH', 'SNAPSHOT_PROTOCOL', 'TOOL_CONFIRMATION', 'TOOL_STALE', 'UNSUPPORTED_SESSION', 'VERIFICATION_REQUIRED', 'TIMEOUT', 'OTHER']:v.pop('error_code',None)
                route_tag={'route_tag':v['route_tag']} if isinstance(v.get('route_tag'),str) and re.fullmatch('[0-9a-f]{16}',v['route_tag']) else {}
                events.append(dict(route_tag,**dict({'error_code':v['error_code']} if 'error_code' in v else {},**{k:x for k,x in v.items() if k in fields and type(x) in (int,float,bool)},event=v['event'],trace=v['trace'])))
            return {'available':True,'events':events,'message':'最近150条脱敏事件；完整记录在项目 logs/gateway-events.jsonl，最多约8 MiB轮转。'}
        except OSError:return {'available':False,'events':[],'message':'日志暂时无法读取；不会因此重发请求。'}

    def get_logs(self):
        with self.lock:return list(self.logs)
    def clear_logs(self):
        with self.lock:self.logs.clear()
        return {'ok':True}
    def close(self):
        self.closing=True
        for p in (self.proc,self.bridge_proc,*self.compat_bridges.values()):
            if p and p.poll() is None:self._terminate(p)
