"""Optional Playwright UI fixture. No live service or browser account access."""
from pathlib import Path

def main():
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]
    html=(root/'desktop/console.html').read_text(encoding='utf-8')
    fixture=r'''<script>
    const fixture={calls:[],supported:true,confirmed:false,row:{alias:'opus5.5',client_id:'a'.repeat(32),account_key:'b'.repeat(64),source_label:'实例5',account_label:'fixture',title:'fixture',online:true,enabled:true,busy:false,uncertain:false,waiting:0}};
    window.confirm=()=>fixture.confirmed;
    const ok=data=>({ok:true,data});
    window.pywebview={api:{boot:async()=>ok({config:{port:9090,api_key:'fixture'},version:'0.5.0',saved_bridges:[],send_settings:{mode:'normal',error:false}}),status:async()=>ok({compatible:true,version:'0.5.0',send_mode_supported:true,send_mode:'normal',tool_reference_supported:fixture.supported}),workspace_status:async()=>ok({sources:[],routes:[fixture.row]}),workspace_action:async(kind,data)=>{if(kind!=='control'||!['reference_on','reference_off'].includes(data.action)||data.confirmed!==true)throw Error('unexpected fixture action');fixture.calls.push(data.action);fixture.row.tool_reference=data.action==='reference_on';return ok({})},gateway_logs:async()=>ok({events:[]}),logs:async()=>ok([])}};
    </script>'''
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:route.abort())
        page.set_content(html.replace('<script>',fixture+'<script>',1))
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_function('state.ready && !state.busy && state.routes.length===1')
        page.locator('.route summary').click()
        on=page.get_by_role('button',name='启用工具文档试验',exact=True)
        on.click();page.wait_for_function('!state.busy');assert page.evaluate('fixture.calls.length')==0
        page.evaluate('fixture.confirmed=true');on.click();page.wait_for_function('!state.busy && state.routes[0].tool_reference===true')
        page.get_by_role('button',name='关闭工具文档试验',exact=True).click();page.wait_for_function('!state.busy && state.routes[0].tool_reference===false')
        assert page.evaluate('fixture.calls')==['reference_on','reference_off']
        page.evaluate('fixture.supported=false;refresh()');page.wait_for_function('state.status.tool_reference_supported===false');assert on.is_disabled()
        page.evaluate("fixture.row.alias='other';refresh()");page.wait_for_function("state.routes[0].alias==='other'");assert on.count()==0
        assert not errors,errors;browser.close()
    print('Reference UI PASS: explicit confirmation, on/off, old gateway guard, alias scope; no network')

if __name__=='__main__':main()
