"""Opt-in Windows Qt check of the actual new console HTML; fixture API only.
No real gateway, bridge, account, socket or chat actions are exposed by this API.
Run directly, not as part of headless discovery.
"""
from pathlib import Path
import json
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    if os.name!='nt':raise RuntimeError('Windows-only native check')
    sys.path.insert(0,str(ROOT/'desktop'))
    from app import configure_desktop_runtime
    configure_desktop_runtime()
    import webview
    def ok(data):return {'ok':True,'data':data}
    source={'client_id':'a'*32,'account_key':'a'*64,'label':'模拟账号','source_label':'实例5','online':True,'transport':'direct'}
    route={'alias':'opus5.5','client_id':'a'*32,'account_key':'a'*64,'title':'模拟已有会话','account_label':'模拟账号','source_label':'实例5','online':False,'enabled':False,'busy':False,'uncertain':False,'waiting':0}
    bridge={'name':'模拟桥','cdp_port':36938}
    calls=[];mode=['normal']
    class Api:
        def boot(self):return ok({'config':{'port':9090,'api_key':'fixture-only'},'version':'0.5.0','send_settings':{'mode':mode[0],'error':False},'saved_bridges':[bridge]})
        def status(self):return ok({'compatible':True,'version':'0.5.0','account_controls_supported':True,'tool_reference_supported':True,'send_mode_supported':True,'send_mode':mode[0],'phase':{'message':'模拟API；没有真实连接'}})
        def workspace_status(self):return ok({'sources':[source],'routes':[route]})
        def workspace_start(self):calls.append('start');return ok({'sources':[source],'routes':[route],'bridge_errors':[]})
        def workspace_action(self,action,data):
            if action=='account' and data.get('account_key')==source['account_key'] and data.get('confirmed') is True:
                source['account_enabled']=data['enabled'];route['account_enabled']=data['enabled'];calls.append('account_on' if data['enabled'] else 'account_off');return ok({})
            if action=='control' and data.get('alias')=='opus5.5' and data.get('confirmed') is True and data.get('action') in ('reference_on','reference_off'):
                route['tool_reference']=data['action']=='reference_on';calls.append(data['action']);return ok({})
            if action=='history':
                calls.append('history');return ok({'rows':[{'context_key':'https://arena.ai/agent/22222222-2222-4222-8222-222222222222','title':'模拟未绑定会话'}],'cursor':None})
            if action!='restore' or data.get('alias')!='opus5.5' or data.get('confirmed') is not True:raise ValueError('Fixture only')
            calls.append('restore');route.update(online=True,enabled=True);return ok({})
        def save_send_mode(self,data):
            if data.get('confirmed') is not True:raise ValueError('Fixture confirmation required')
            mode[0]=data['mode'];calls.append('mode');return ok({'mode':mode[0],'error':False})
        def gateway_logs(self):return ok({'events':[],'message':'fixture'})
        def logs(self):return ok([])
    html=(ROOT/'desktop/console.html').read_text(encoding='utf-8')
    html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="connect-src \'none\'; img-src data:;">',1)
    window=webview.create_window('Arena2API 0.6.0 账号视图离线验证（自动关闭）',html=html,js_api=Api(),minimized=True,focus=False,width=1240,height=840)
    result={'ok':False,'actual_console_html':True,'fixture_api':True,'gateway_started':False,'account_requests':0}
    def until(expression):
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if window.evaluate_js(expression):return
            time.sleep(.15)
        raise RuntimeError('Timed out: '+expression)
    def verify():
        try:
            if not window.events.loaded.wait(25):raise RuntimeError('Document load timed out')
            until("typeof state!=='undefined' && state.ready && document.querySelectorAll('.source').length===1 && document.querySelectorAll('.route').length===1")
            assert window.evaluate_js("document.getElementById('version').textContent.includes('0.6.0')")
            assert calls==[], 'Boot performed an action'
            assert window.evaluate_js("document.querySelectorAll('.account').length===1")
            window.evaluate_js("document.getElementById('addAccount').click()")
            assert window.evaluate_js("!document.getElementById('enrollment').classList.contains('hidden')")
            assert calls==[], 'Opening onboarding performed an action'
            window.evaluate_js("document.getElementById('closeEnrollment').click()")
            assert window.evaluate_js("document.querySelector('#home h1').textContent.includes('账号与会话')")
            assert window.evaluate_js("document.querySelector('#savedBridges').textContent.includes('模拟桥')")
            assert window.evaluate_js("document.getElementById('version').textContent.includes('0.5.0')")
            window.evaluate_js("document.getElementById('startWorkspace').click()")
            until("state.busy===false && state.routes[0].online===false")
            assert calls==['start'],'Connection start must not restore disabled routes'
            window.evaluate_js("document.querySelector('.route [role=switch]').click()")
            until("state.busy===false && state.routes[0].online===true")
            assert calls==['start','restore'],calls
            window.evaluate_js("document.getElementById('sendMode').value='full';document.getElementById('saveSendMode').click()")
            until("state.busy===false && document.getElementById('sendModeLive').textContent.includes('当前网关：携带客户端上下文')")
            assert calls==['start','restore','mode'],calls
            window.evaluate_js("window.confirm=()=>true;[...document.querySelectorAll('.route button')].find(b=>b.textContent==='启用工具文档试验').click()")
            until("state.busy===false && state.routes[0].tool_reference===true")
            window.evaluate_js("[...document.querySelectorAll('.route button')].find(b=>b.textContent==='关闭工具文档试验').click()")
            until("state.busy===false && state.routes[0].tool_reference===false")
            assert calls==['start','restore','mode','reference_on','reference_off'],calls
            source['history_enabled']=True
            window.evaluate_js('refresh()')
            until("document.querySelectorAll('.catalog-row').length===1 && state.busy===false")
            assert calls==['start','restore','mode','reference_on','reference_off','history'],calls
            window.evaluate_js("document.querySelector('.catalog-row button').click()")
            assert window.evaluate_js("!document.getElementById('chooser').classList.contains('hidden') && document.getElementById('bind').textContent==='启用会话并暴露模型'")
            assert window.evaluate_js("!document.body.textContent.includes('实例 5 桥接')")
            window.evaluate_js("document.getElementById('closeChooser').click()")
            window.evaluate_js("document.querySelector('.account-item [role=switch]').click()")
            until("state.busy===false && state.sources[0].account_enabled===false")
            assert route['enabled'] is True
            window.evaluate_js("document.querySelector('.account-item [role=switch]').click()")
            until("state.busy===false && state.sources[0].account_enabled===true")
            assert calls[-2:]==['account_off','account_on']
            assert window.evaluate_js("getComputedStyle(document.body).backgroundColor==='rgb(16, 16, 16)'")
            result.update(account_gate_preserves_conversation=True,monochrome_theme=True,ok=True,renderer='qtwebengine',boot_no_actions=True,explicit_conversation_enable=True,saved_bridge_visible=True,auto_read_opted_source=True,no_auto_send_authorization=True,reference_toggle_no_send=True)
        except Exception as exc:
            result.update(error_type=type(exc).__name__,error=str(exc)[:400],fixture_calls=calls)
            try:result['fixture_ui_state']=window.evaluate_js("({ready:state.ready,busy:state.busy,sourceCount:state.sources.length,routeCount:state.routes.length,error:document.getElementById('error').textContent})")
            except Exception:pass
        finally:window.destroy()
    folder=ROOT/'.test-tmp';folder.mkdir(exist_ok=True)
    webview.start(verify,gui='qt',debug=False,private_mode=True,storage_path=str(folder/'workspace-smoke-webview'))
    (folder/'workspace-native-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok']:raise SystemExit(1)

if __name__=='__main__':main()
