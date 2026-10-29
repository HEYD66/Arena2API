"""Arena2API conversation-only gateway. No model catalog or legacy Arena sender."""
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai_compat import ProtocolError
from conversation_bridge import install_conversation_routes
import gateway_log
from source_router import SourceRouter
from desktop.send_settings import SendModeSettings
from tool_filter import ToolFilter

VERSION = '0.5.0'
PRODUCT = 'arena2api-conversations'
ROOT = Path(__file__).resolve().parents[1]

def local_key():
    if os.environ.get('API_KEY'):
        return os.environ['API_KEY']
    try:
        value=json.loads((ROOT/'.console/config.json').read_text(encoding='utf-8')).get('api_key')
        if isinstance(value,str) and value:return value
    except (ValueError,OSError):pass
    return 'arena-local'

API_KEY=local_key()
@asynccontextmanager
async def lifespan(app):
    gateway_log.configure(ROOT/'logs/gateway-events.jsonl')
    yield

app=FastAPI(title='Arena2API · 会话路由',version=VERSION,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['GET','POST','OPTIONS'],allow_headers=['Authorization','Content-Type'])

def verify_api_key(request):
    supplied=request.headers.get('authorization','')
    if not secrets.compare_digest(supplied.encode(),('Bearer '+API_KEY).encode()):
        raise HTTPException(401,'本地 API Key 不正确')

conversation_bridge=install_conversation_routes(app,verify_api_key)
source_router=SourceRouter(ROOT/'.console/routes.json')
conversation_bridge.sources_router=source_router
send_mode_settings=SendModeSettings(ROOT/'.console/send-mode.json')
conversation_bridge.send_mode_provider=send_mode_settings.get_mode
source_router.send_mode_provider=send_mode_settings.get_mode
tool_filter=ToolFilter(ROOT/'.console/tool-filter.json')
source_router.tool_filter=tool_filter
conversation_bridge.tool_filter_provider=lambda tools:tool_filter.apply('arena-bound',tools)
app.add_middleware(gateway_log.TraceMiddleware)


@app.exception_handler(ProtocolError)
async def protocol_error(request,exc):return JSONResponse(status_code=exc.status,content=exc.payload())

@app.exception_handler(HTTPException)
async def http_error(request,exc):
    return JSONResponse(status_code=exc.status_code,headers=exc.headers,content={'error':{
        'message':str(exc.detail),'type':'server_error' if exc.status_code>=500 else 'invalid_request_error',
        'code':str(exc.status_code),'param':None}})

@app.get('/health')
async def health():
    import time
    mode=send_mode_settings.status()
    return {'account_controls_supported':True,'tool_reference_supported':True,'send_mode_supported':True,'send_mode':mode['mode'],'send_mode_error':mode['error'],'status':'ok','product':PRODUCT,'version':VERSION,
            'extension_connected':any(time.time()-o['seen']<45 for o in conversation_bridge.offers.values()) or any(time.time()-o['seen']<15 for o in source_router.sources.values()),
            'model_count':len(source_router.advertised_models()) if source_router.sources or source_router.routes or source_router.account_preferences or source_router.account_preferences_error or getattr(source_router,'load_error',False) else 1,'mode':'existing-conversation','logging':gateway_log.health()}

@app.get('/v1/models')
async def models(request:Request):
    verify_api_key(request)
    return {'object':'list','data':[{'id':alias,'object':'model','created':0,'owned_by':PRODUCT} for alias in (source_router.advertised_models() if source_router.sources or source_router.routes or source_router.account_preferences or source_router.account_preferences_error or getattr(source_router,'load_error',False) else ['arena-bound'])]}

@app.post('/v1/chat/completions')
async def chat_completions(request:Request):
    verify_api_key(request)
    raw=await request.body()
    if len(raw)>1600000:raise HTTPException(413,'请求超过本地接收上限')
    try:body=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,'Invalid JSON')
    if not isinstance(body,dict):raise HTTPException(400,'请求必须是JSON对象')
    if isinstance(body.get('model'),str) and body['model'] in source_router.routes:
        return await source_router.completion(body,request)
    if body.get('model')!='arena-bound':
        raise HTTPException(400,'未配置此模型别名；请在控制台绑定已有会话，不支持旧模型目录或自动新建')
    if source_router.sources or source_router.routes or source_router.account_preferences or source_router.account_preferences_error or getattr(source_router,'load_error',False):raise HTTPException(409,'请先在绑定列表中创建或恢复 arena-bound 别名；不会回退到旧目标')
    return await conversation_bridge.completion(body,request=request)

@app.get('/v1/workspace/status')
async def workspace_status(request:Request):
    verify_api_key(request)
    return source_router.status()

@app.post('/v1/workspace/{action}')
async def workspace_action(action:str,request:Request):
    verify_api_key(request)
    raw=await request.body()
    if len(raw)>16000:raise HTTPException(413,'控制请求过大')
    try:data=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,'JSON无效')
    if not isinstance(data,dict):raise HTTPException(400,'需要JSON对象')
    actions={'account':source_router.account_control,'rename':source_router.rename,'history':source_router.history,'bind':source_router.bind,'restore':source_router.restore,'control':source_router.control}
    if action not in actions:raise HTTPException(404,'未知操作')
    return await actions[action](data)

@app.get('/')
async def index():
    return {'product':PRODUCT,'version':VERSION,'message':'请在本地控制台选择账号来源，绑定已有会话和模型别名；DSH按别名调用。'}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('PORT','9090')))
