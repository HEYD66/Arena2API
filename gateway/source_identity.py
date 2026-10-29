"""Source identity validation; never treats a display name as account identity."""
import re
from fastapi import HTTPException
CLIENT=re.compile(r'^[0-9a-f]{32}$')
ACCOUNT=re.compile(r'^[0-9a-f]{64}$')
ALIAS=re.compile(r'^[a-z][a-z0-9._-]{0,63}$')
def clean_source(data):
    if not isinstance(data,dict) or not CLIENT.fullmatch(str(data.get('client_id',''))) or not CLIENT.fullmatch(str(data.get('session',''))):
        raise HTTPException(400,'扩展来源标识无效')
    account=data.get('account_key')
    if account is None and data.get('identity_status')=='unavailable':pass
    elif not isinstance(account,str) or not ACCOUNT.fullmatch(account):raise HTTPException(409,'账号身份尚未核验，不能提供发送来源')
    return {'client_id':data['client_id'],'session':data['session'],'account_key':account,
            'label':str(data.get('label') or 'Arena账号')[:80],
            'source_label':str(data.get('source_label') or '浏览器扩展')[:80],
            'history_enabled':data.get('history_enabled') is True,
            'transport':'bridge' if data.get('transport')=='bridge' else 'direct'}
