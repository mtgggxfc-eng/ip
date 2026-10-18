import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

root=pathlib.Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('uninstaller_test',root/'xiezai.py')
uninstaller=importlib.util.module_from_spec(spec); sys.modules[spec.name]=uninstaller; spec.loader.exec_module(uninstaller)

def put(base,name,value):
    path=base/name.lstrip('/'); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(value,encoding='utf-8'); return path

def fixture(base):
    for name,signatures in uninstaller.UNIT_SIGNATURES.items():
        commands=[s if not s.startswith('^') else ('ExecStart=/opt/residential-stack-bin/sing-box run -c /etc/residential-stack/ws.json' if name == 'residential-ws.service' else 'ExecStart=/opt/residential-stack-bin/sing-box run -c /etc/sing-box/three-nodes.json') for s in signatures]
        put(base,'/etc/systemd/system/'+name,'\n'.join(commands)+'\n')
    put(base,'/etc/residential-stack/panel.py','server_version = "ResidentialPanel/1.0"')
    put(base,'/etc/residential-stack/settings.json','{}')
    put(base,'/etc/residential-stack/auth.json','{"password":"test-only"}')
    put(base,'/etc/residential-stack/acme/live/panel.pem','own-panel-certificate')
    put(base,'/opt/residential-stack-agent/lite_manager.py','WORKSPACE = Path("/opt/residential-stack-agent")\nC2_URL="local"')
    put(base,'/etc/sing-box/three-nodes.json',json.dumps({'inbounds':[{'tag':'reality-home'}]}))
    put(base,'/opt/residential-stack-bin/sing-box','binary')
    put(base,'/opt/residential-certbot/pyvenv.cfg','home = /usr/bin')
    put(base,'/opt/residential-certbot/bin/certbot','binary')
    put(base,'/root/three-nodes-links.txt','vless://tcp\nhysteria2://udp\nvless://home\n')
    put(base,'/root/three-nodes-info.txt','TCP 节点状态：ok\n住宅管理面板：https://example.test')
    originals={
        '/etc/systemd/system/sing-box.service':'original-singbox',
        '/etc/systemd/system/x-ui.service':'original-xui',
        '/etc/systemd/system/proxy-lite.service':'original-agent',
        '/opt/proxy_lite/lite_manager.py':'original-agent-source',
        '/etc/s-box/sb.json':'original-node-config',
        '/etc/sing-box/hy2-cert.pem':'potentially-shared-certificate',
        '/etc/sing-box/hy2-key.pem':'potentially-shared-key',
        '/usr/bin/sing-box':'original-binary',
    }
    for name,value in originals.items(): put(base,name,value)
    return originals

with tempfile.TemporaryDirectory(dir=root/'tests') as directory:
    base=pathlib.Path(directory)
    originals=fixture(base)
    calls=[]
    def runner(args,check=False):
        calls.append(args)
        rules=[{'table':190,'oif':'tun90','priority':32765},{'table':190,'oif':'tun0','priority':32766}]
        return subprocess.CompletedProcess(args,0,json.dumps(rules) if args==['ip','-j','rule','show'] else '','')
    plan=uninstaller.inventory(base)
    assert not plan.conflicts,plan.conflicts
    assert len(plan.units)==len(uninstaller.UNIT_SIGNATURES)
    uninstaller.execute(plan,runner)
    for path in plan.files+plan.directories: assert not path.exists(),path
    for name,value in originals.items(): assert (base/name.lstrip('/')).read_text(encoding='utf-8')==value,name
    assert ['ip','rule','del','pref','32765','table','190'] in calls
    assert not any('32766' in call for call in calls)
    assert not any('sing-box.service' in call or 'proxy-lite.service' in call or 'x-ui.service' in call for call in calls)
    again=uninstaller.inventory(base)
    assert not again.files and not again.directories and not again.units

for collision in ('service','node-config'):
    with tempfile.TemporaryDirectory(dir=root/'tests') as directory:
        base=pathlib.Path(directory); fixture(base)
        if collision=='service': put(base,'/etc/systemd/system/three-nodes.service','ExecStart=/unrelated/program')
        else: put(base,'/etc/sing-box/three-nodes.json',json.dumps({'inbounds':[{'tag':'someone-elses-node'}]}))
        plan=uninstaller.inventory(base); assert plan.conflicts
        calls=[]
        try:
            uninstaller.execute(plan,lambda *args,**kwargs:calls.append(args))
            raise AssertionError('unsafe uninstall was allowed')
        except RuntimeError: pass
        assert not calls and (base/'etc/residential-stack/auth.json').exists()
print('uninstall scope, original services, shared certificates, owned route cleanup, repeat runs and collision guards: OK')
