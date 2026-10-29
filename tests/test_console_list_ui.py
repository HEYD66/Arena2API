"""Browser acceptance of production console with an offline mocked desktop API."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    from playwright.sync_api import sync_playwright
    checks=0
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('http://**/*',lambda r:r.abort());page.route('https://**/*',lambda r:r.abort())
        page.add_init_script((ROOT/'tests/console_list_fixture.js').read_text())
        page.goto((ROOT/'desktop/console.html').as_uri());page.wait_for_function('state.ready && state.sources.length===2')
        assert page.locator('.account-item').count()==3 and page.locator('.conversation-table .route').count()==3;checks+=1
        assert page.evaluate('consoleFixture.calls.length')==0;checks+=1
        assert page.locator('.conversation-table th').all_text_contents()==['会话','对外模型名','运行状态','启用会话'];checks+=1
        gate=page.get_by_role('switch',name='启用此账号：开发账号',exact=True)
        page.evaluate("()=>{window.originalStatus=pywebview.api.status;pywebview.api.status=()=>new Promise(resolve=>window.releaseStatus=()=>resolve(originalStatus()))}")
        gate.click();page.wait_for_function('typeof window.releaseStatus===\"function\"')
        assert page.evaluate('state.busy') and gate.is_disabled();checks+=1
        page.evaluate('pywebview.api.status=originalStatus;releaseStatus()')
        page.wait_for_function('!state.busy && state.sources[0].account_enabled===false')
        assert page.get_by_role('switch',name='启用此会话：主项目 · 开发与联调',exact=True).get_attribute('aria-checked')=='true';checks+=1
        assert '账号已停用' in page.locator('.conversation-table').inner_text();checks+=1
        gate.click();page.wait_for_function('!state.busy && state.sources[0].account_enabled===true')
        conversation=page.get_by_role('switch',name='启用此会话：文档与方案整理',exact=True)
        conversation.click();page.wait_for_function("!state.busy && state.routes.find(r=>r.alias==='writing').enabled")
        assert page.evaluate("consoleFixture.calls.at(-1)[0]")=='restore';checks+=1
        conversation.click();page.wait_for_function("!state.busy && !state.routes.find(r=>r.alias==='writing').enabled");checks+=1
        page.get_by_role('button',name='修改模型名',exact=True).first.click();page.locator('#modelName').fill('coding-review')
        assert page.locator('#applyModel').is_disabled() and page.locator('#modelDialogError').is_visible();checks+=1
        page.locator('#modelName').fill('assistant-dev');page.locator('#applyModel').click()
        page.wait_for_function("!state.busy && state.routes[0].alias==='assistant-dev'")
        assert page.locator('.alias').first.inner_text()=='assistant-dev';checks+=1
        page.locator('#startWorkspace').click();page.wait_for_function('!state.busy')
        assert page.evaluate("!state.routes.find(r=>r.alias==='writing').enabled");checks+=1
        page.locator('.account-select').filter(has_text='日常工作').click()
        assert page.locator('.route').count()==1 and page.locator('.alias').inner_text()=='research';checks+=1
        page.locator('.account-select').filter(has_text='开发账号').click()
        page.locator('.source button').click();page.wait_for_function('!state.busy && state.catalogs.size===1')
        assert page.locator('.catalog-row').count()==2 and not page.evaluate('window.attacked===true');checks+=1
        page.locator('.catalog-row button').first.click();page.locator('#alias').fill('new-model');assert page.locator('#bind').is_enabled() and not page.evaluate("consoleFixture.calls.some(c=>c[0]==='bind')");checks+=1
        page.locator('#bind').click();page.wait_for_function("!state.busy && state.routes.some(r=>r.alias==='new-model')");checks+=1
        assert not page.evaluate("consoleFixture.calls.some(c=>c[0]==='send')");checks+=1
        page.evaluate("state.catalogs.clear();state.catalogRevision++;consoleFixture.routes[0].busy=true;consoleFixture.routes[0].uncertain=true;refresh()")
        page.wait_for_function('!polling')
        assert page.get_by_role('switch',name='启用此会话：主项目 · 开发与联调',exact=True).is_disabled();checks+=1
        assert page.get_by_role('button',name='修改模型名',exact=True).first.is_disabled();checks+=1
        page.locator('#addAccount').click();assert page.locator('#enrollment').is_visible();checks+=1
        page.locator('#wizardBridge').click();assert page.locator('#bridgeDialog').is_visible();checks+=1
        assert page.locator('#bridgeStart').is_disabled();checks+=1
        page.locator('#closeBridge').click();page.locator('#closeEnrollment').click()
        page.locator('[data-page=settings]').click();page.locator('#sendMode').select_option('full');page.locator('#saveSendMode').click()
        page.wait_for_function("!state.busy && consoleFixture.mode==='full'");checks+=1
        assert page.locator('#savedBridges .bridge-row').count()==2;checks+=1
        page.locator('[data-page=diagnostics]').click();page.locator('#refreshGatewayLogs').click();page.wait_for_function('!state.busy');checks+=1
        page.locator('[data-page=home]').click()
        for width in [1440,1024,768]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');checks+=1
        page.set_viewport_size({'width':1440,'height':1000})
        page.evaluate("consoleFixture.routes[0].busy=false;consoleFixture.routes[0].uncertain=false;refresh()")
        page.wait_for_function('!polling');page.screenshot(path=str(ROOT/'.test-tmp/console-list-desktop.png'),full_page=True)
        assert not errors,errors;checks+=1
        browser.close()
    print(str(checks)+' browser UI checks PASS; fixture API only; all HTTP blocked')
if __name__=='__main__':main()
