"""Real Chromium DOM, mocked extension runtime. No account/network calls.
The fixture alone captures the closed shadow root to inspect it. Production
never exports that root or any extension settings into the page world.
"""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOCK=r"""
window.fixture={calls:[],listeners:[],timers:0,saveError:false,offline:false,config:{enabled:true,offered:true,url:'http://127.0.0.1:9090',source:'fixture browser',account:'fixture account'}};
const originalAttach=Element.prototype.attachShadow;
Element.prototype.attachShadow=function(options){const root=originalAttach.call(this,options);window.fixture.root=root;return root;};
const nativeInterval=window.setInterval,nativeClear=window.clearInterval;
window.setInterval=(fn,ms)=>{fixture.timers++;return nativeInterval(fn,ms)};
window.clearInterval=id=>{if(id!==null&&id!==undefined)fixture.timers--;nativeClear(id)};
window.chrome={runtime:{id:'fixture-extension',lastError:null,onMessage:{addListener:fn=>fixture.listeners.push(fn)},sendMessage:(m,cb)=>{
 fixture.calls.push(m);
 if(fixture.offline){chrome.runtime.lastError={message:'fixture disconnected'};cb();chrome.runtime.lastError=null;return;}
 if(m.type==='CONVERSATION_STATUS'){cb({...fixture.config});return;}
 if(m.type==='WORKSPACE_CONFIG'){
  if(fixture.saveError){cb({ok:false,error:'fixture save failed'});return;}
  Object.assign(fixture.config,{enabled:m.enabled,url:m.url});if(m.label!==undefined)fixture.config.source=m.label;cb({ok:true});return;
 }
 if(m.type==='WORKSPACE_RESET'){fixture.config.enabled=false;cb({ok:true});return;}
 throw Error('unexpected operation');
}}};
window.el=id=>fixture.root.getElementById(id);
window.click=id=>el(id).click();
window.toggle=()=>fixture.listeners.forEach(fn=>fn({type:'WORKSPACE_PANEL_TOGGLE'},{id:'fixture-extension'},()=>{}));
"""

def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body='<!doctype html><html><body style="background:#202020;color:white"><h1>Fixture Arena page</h1><input id="chat" placeholder="fixture input"><button id="underlying">Underlying page</button><script>window.baseClicks=0;document.getElementById("underlying").onclick=()=>baseClicks++</script></body></html>'))
        context.add_init_script(MOCK)
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('https://arena.ai/agent/fixture')
        code=(ROOT/'extension/chrome/conversation/panel.js').read_text()
        page.evaluate(code)
        assert page.evaluate('el("panel").hidden && fixture.calls.length===0 && fixture.timers===0')
        assert page.evaluate('document.getElementById("arena2api-settings-host").shadowRoot===null')
        page.locator('#underlying').click();assert page.evaluate('baseClicks===1')
        page.evaluate('click("entry")');page.wait_for_function('el("status").textContent.includes("浏览器在线")')
        assert page.evaluate('!el("panel").hidden && fixture.timers===1 && el("url").value==="http://127.0.0.1:9090"')
        assert page.evaluate('el("label")===null && el("enabled")===null && el("save")===null')
        assert page.evaluate('el("toggle").getAttribute("aria-pressed")==="true" && el("url").disabled')
        page.evaluate('click("toggle")');page.wait_for_function('!el("toggle").disabled')
        assert page.evaluate('fixture.config.enabled===false && !el("url").disabled')
        page.evaluate('el("url").value="http://127.0.0.1:9191";click("toggle")');page.wait_for_function('!el("toggle").disabled')
        assert page.evaluate('fixture.config.enabled===true && fixture.config.url==="http://127.0.0.1:9191" && fixture.config.source==="fixture browser"')
        page.evaluate('click("close")')
        assert page.evaluate('el("panel").hidden && fixture.timers===0 && fixture.config.enabled===true')
        assert page.evaluate('fixture.calls.filter(m=>m.type==="WORKSPACE_CONFIG").length===2')
        page.evaluate('toggle()');assert page.evaluate('!el("panel").hidden && fixture.timers===1')
        page.evaluate('toggle()');assert page.evaluate('el("panel").hidden && fixture.timers===0')
        page.evaluate('click("entry");el("advanced").open=true;click("reset");click("cancelReset")')
        assert page.evaluate('!fixture.calls.some(m=>m.type==="WORKSPACE_RESET")')
        page.evaluate('fixture.saveError=true;click("toggle")');page.wait_for_function('!el("toggle").disabled')
        assert page.evaluate('!el("error").hidden && el("error").textContent.includes("fixture save failed") && fixture.config.enabled===true')
        page.evaluate('fixture.saveError=false;click("close");fixture.offline=true;click("entry")')
        page.wait_for_function('el("error").textContent.includes("刷新")')
        page.evaluate('fixture.offline=false;click("close");click("entry")')
        page.wait_for_function('el("status").textContent.includes("浏览器在线")')
        # Every tab owns a different DOM entry. Opening a second page cannot toggle this one.
        other=context.new_page();other.goto('https://arena.ai/agent/second-fixture');other.evaluate(code)
        assert other.evaluate('el("panel").hidden && fixture.calls.length===0')
        other.evaluate('click("entry")');other.evaluate('click("close")')
        assert page.evaluate('!el("panel").hidden')
        # Re-injection must not duplicate the entry or message handler.
        page.evaluate(code);assert page.evaluate('document.querySelectorAll("#arena2api-settings-host").length===1 && fixture.listeners.length===1')
        page.set_viewport_size({'width':390,'height':650})
        assert page.evaluate('el("panel").getBoundingClientRect().left>=0 && el("panel").getBoundingClientRect().right<=innerWidth && document.documentElement.scrollWidth<=innerWidth')
        page.evaluate('el("close").dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,composed:true}))')
        assert page.evaluate('el("panel").hidden && fixture.timers===0')
        page.evaluate('click("entry");el("advanced").open=true;click("reset");click("confirmReset")')
        page.wait_for_function('el("status").textContent.includes("已停用并重置")')
        assert page.evaluate('fixture.calls.filter(m=>m.type==="WORKSPACE_RESET").length===1 && fixture.timers===0 && el("toggle").getAttribute("aria-pressed")==="false"')
        assert page.evaluate('fixture.calls.every(m=>["CONVERSATION_STATUS","WORKSPACE_CONFIG","WORKSPACE_RESET"].includes(m.type))')
        assert len(context.pages)==2 and not errors,errors
        browser.close()
    print('Page-local settings UI PASS: lifecycle, isolation, save/error, explicit reset, narrow viewport; mocked runtime; no account requests')

if __name__=='__main__':main()
