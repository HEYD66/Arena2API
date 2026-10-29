"""Refinement acceptance: real DOM + offline fixture; no gateway or model requests."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':1280,'height':900});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('http://**/*',lambda r:r.abort());page.route('https://**/*',lambda r:r.abort())
        page.add_init_script((ROOT/'tests/console_list_fixture.js').read_text())
        page.goto((ROOT/'desktop/console.html').as_uri());page.wait_for_function('state.ready&&state.sources.length===2')
        assert page.locator('.conversation-table').bounding_box()['y']<340
        assert page.locator('.model-edit').count()==3
        page.locator('.connection-details summary').click();assert page.locator('#phase').is_visible()
        page.locator('.connection-details summary').click()
        def change(code):
            page.evaluate('Object.assign(consoleFixture.routes[2],'+code+');refresh()');page.wait_for_function('!polling')
        change('{uncertain:true,pending_review:true,online:false,review_available:true}')
        row=page.locator('.route').filter(has_text='writing');review=row.get_by_role('button',name='核对上轮',exact=True)
        switch=row.get_by_role('switch')
        assert switch.is_disabled() and review.is_visible() and review.is_enabled()
        assert row.locator('details[open]').count()==0 and row.get_by_role('button',name='核对上轮').count()==1
        assert '先核对上轮' in row.locator('.switch-reason').inner_text()
        assert switch.get_attribute('aria-describedby')==row.locator('.switch-reason').get_attribute('id')
        review.click();page.wait_for_function('!state.busy')
        assert page.evaluate("consoleFixture.calls.at(-1)[0]==='control'&&consoleFixture.calls.at(-1)[1].action==='check'")
        assert switch.is_disabled() and not page.evaluate("consoleFixture.calls.some(c=>c[0]==='restore')")
        for state,reason in [('{busy:true}','执行'),('{busy:false,waiting:1}','排队'),('{waiting:0,review_available:false}','浏览器在线后')]:
            change(state);assert review.is_disabled() and reason in row.inner_text()
        change('{review_available:true}')
        for width in [1280,1024,768,375]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert review.is_visible() and switch.is_disabled()
            if width in [1280,375]:page.screenshot(path=str(ROOT/f'.test-tmp/refinement-{width}.png'),full_page=True)
        assert not errors,errors
        assert not page.evaluate("consoleFixture.calls.some(c=>['send','restore','account','bind','start'].includes(c[0]))")
        browser.close()
    print('Refinement PASS: compact hierarchy, visible review/reasons, busy/waiting/offline guards, 4 widths, zero live HTTP')
if __name__=='__main__':main()
