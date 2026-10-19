"""UI removal regression using mock machines only; never deletes real resources."""
import json,pathlib,threading,tempfile,subprocess
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from functools import partial
from playwright.sync_api import sync_playwright,expect
ROOT=pathlib.Path(__file__).resolve().parent.parent
temporary=tempfile.TemporaryDirectory(prefix='ip-hub-browser-')
folder=pathlib.Path(temporary.name)
subprocess.run(['node',str(ROOT/'tests/test_hub.mjs'),str(folder/'hub-preview.html')],cwd=ROOT,check=True)
server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(folder)))
threading.Thread(target=server.serve_forever,daemon=True).start()
machines=[{'id':'legacy','label':'旧分组'},{'id':'m-one','label':'保留机器'},{'id':'m-two','label':'待删除机器'}]
calls=[];fail=True
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def api(route):
        global fail,machines
        req=route.request;path=req.url.split('/api/',1)[1].split('?')[0]
        if path.startswith('machines/') and req.method=='DELETE':
            assert req.headers.get('x-panel-action')=='1'
            calls.append(path)
            if fail:return route.fulfill(status=502,json={'error':'cleanup unavailable'})
            target=path.split('/')[-1];machines=[m for m in machines if m['id']!=target]
            return route.fulfill(json={'ok':True})
        if path=='machines':data=machines
        elif path=='config':data={'slot_map':{'0':'US'},'proxy_port':12345,'mode':'auto','force_switch':{},'manual_node_ip':'','youtube_check':False,'config_fetch_interval':15,'heartbeat_interval':30,'frontend_poll_interval':5}
        elif path=='countries':data=['US','JP']
        elif path=='nodes':data=[]
        else:data={'ok':True}
        route.fulfill(json=data)
    page.route('**/api/**',api)
    page.goto('http://127.0.0.1:'+str(server.server_port)+'/hub-preview.html')
    expect(page.locator('#machine-picker')).to_have_value('legacy')
    expect(page.locator('#remove-machine')).to_be_disabled()
    page.select_option('#machine-picker','m-two')
    expect(page.locator('#remove-machine')).to_be_enabled()
    messages=[]
    def cancel(d):messages.append(d.message);d.dismiss()
    page.once('dialog',cancel);page.locator('#remove-machine').click()
    assert not calls and '待删除机器' in messages[0] and '节点及面板会断开' in messages[0]
    page.once('dialog',lambda d:d.accept());page.locator('#remove-machine').click()
    expect(page.locator('#config-form')).to_contain_text('删除尚未完成')
    expect(page.locator('#remove-machine')).to_be_enabled()
    expect(page.locator('button[onclick="saveConfig()"]').first).to_be_disabled()
    assert calls==['machines/m-two'] and len(machines)==3
    fail=False
    page.once('dialog',lambda d:d.accept());page.locator('#remove-machine').click()
    expect(page.locator('#machine-picker')).to_have_value('legacy')
    assert [m['id'] for m in machines]==['legacy','m-one']
    assert calls==['machines/m-two','machines/m-two']
    assert page.locator('#machine-picker option[value="m-two"]').count()==0
    assert not errors,errors
    browser.close()
server.shutdown();server.server_close();temporary.cleanup()
print('Delete UI: legacy disabled, named warning, cancellation, exact machine only, partial-failure retry, save blocked, list refreshed passed')
