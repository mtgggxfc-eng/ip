#!/usr/bin/env python3
"""One-VPS residential exit control panel. Python standard library only."""
import base64
import hmac
import ipaddress
import json
import os
import pathlib
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path('/etc/residential-stack')
SETTINGS = ROOT / 'settings.json'
AUTH = ROOT / 'auth.json'
STATUS = ROOT / 'status.json'
SINGBOX_CONFIG = pathlib.Path('/etc/sing-box/three-nodes.json')
LOCK = threading.RLock()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def config_for_agent(settings):
    return {
        'mode': settings['mode'],
        'manual_node_ip': settings['manual_node_ip'],
        'proxy_port': settings['proxy_port'],
        'slot_map': {'0': settings['country']},
        'force_switch': {'0': settings['switch_revision']},
        'config_fetch_interval': 15,
        'heartbeat_interval': 30,
        'youtube_check': False,
    }


PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>住宅出口管理</title>
<style>body{font-family:system-ui,sans-serif;background:#0b1220;color:#e9f0fa;margin:0;padding:24px}main{max-width:700px;margin:auto}h1{font-size:26px}p{color:#a9bbd1;line-height:1.6}.card{background:#172338;border:1px solid #30415b;border-radius:14px;padding:20px;margin:20px 0}label{display:block;margin:14px 0 6px}input,select,button{font:inherit;border-radius:8px;padding:10px}input,select{box-sizing:border-box;width:100%;background:#0e192a;color:#fff;border:1px solid #50627a}button{border:0;background:#2c77e5;color:#fff;cursor:pointer;margin:16px 10px 0 0}button.secondary{background:#53677e}code{color:#7ee5ba;word-break:break-all}pre{white-space:pre-wrap;overflow:auto;max-height:330px;color:#afc4d9}#msg{color:#7ee5ba}</style></head><body><main><h1>住宅出口管理</h1><p>这台 VPS 独立管理。国家、住宅代理内部端口和出口 IP 可以在此调整。节点链接保存在 VPS 的 <code>/root/three-nodes-info.txt</code>。</p>
<div class="card"><h2>当前状态</h2><pre id="status">正在读取…</pre></div>
<div class="card"><h2>出口设置</h2><label>目标国家（两位国家代码，如 US、JP、SG）</label><input id="country" maxlength="2" autocapitalize="characters"><label>住宅代理内部端口</label><input id="port" type="number" min="1024" max="65535"><label>出口选择</label><select id="mode"><option value="auto">自动选择</option><option value="manual">固定指定 IP</option></select><label>指定出口 IP（自动选择时留空）</label><input id="manual" placeholder="例如 1.2.3.4"><button onclick="save()">保存设置</button><button class="secondary" onclick="rotate()">立即更换家宽 IP</button><div id="msg"></div></div>
<div class="card"><h2>最近代理日志</h2><pre id="logs">加载中…</pre></div></main><script>
async function api(path,options){let r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Panel-Action':'1',...(options?.headers||{})}});let j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
async function load(){try{let [c,s]=await Promise.all([api('/api/config'),api('/api/status')]);document.getElementById('country').value=c.slot_map['0'];document.getElementById('port').value=c.proxy_port;document.getElementById('mode').value=c.mode;document.getElementById('manual').value=c.manual_node_ip;let d=s.details?.[0];document.getElementById('status').textContent=d?`出口国家：${d.country}\n出口 IP：${d.node_ip}\n内部端口：${d.port}\n上报时间：${new Date(s.updated_at*1000).toLocaleString()}`:'住宅出口尚未就绪，请查看日志';document.getElementById('logs').textContent=s.log||'暂无日志'}catch(e){document.getElementById('msg').textContent=e.message}}
async function save(){let p={country:document.getElementById('country').value.trim().toUpperCase(),proxy_port:Number(document.getElementById('port').value),mode:document.getElementById('mode').value,manual_node_ip:document.getElementById('manual').value.trim()};try{await api('/api/config',{method:'POST',body:JSON.stringify(p)});document.getElementById('msg').textContent='已保存，出口切换可能需要一些时间';await load()}catch(e){document.getElementById('msg').textContent=e.message}}
async function rotate(){try{await api('/api/switch',{method:'POST',body:'{}'});document.getElementById('msg').textContent='已发出更换指令，请稍后刷新状态'}catch(e){document.getElementById('msg').textContent=e.message}}
load();setInterval(load,30000)</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = 'ResidentialPanel/1.0'

    def reply(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        credentials = read_json(AUTH)
        expected = base64.b64encode((credentials['username'] + ':' + credentials['password']).encode()).decode()
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied, 'Basic ' + expected):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Residential Exit Panel"')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return False
        return True

    def body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 2_000_000 or size < 0:
            raise ValueError('请求过大')
        return json.loads(self.rfile.read(size).decode('utf-8'))

    def do_GET(self):
        if not self.authorized(): return
        with LOCK:
            if self.path == '/api/config':
                return self.reply(200, config_for_agent(read_json(SETTINGS)))
            if self.path == '/api/status':
                return self.reply(200, read_json(STATUS) if STATUS.exists() else {'details': [], 'log': '', 'updated_at': 0})
            if self.path not in ('/', '/index.html'):
                return self.reply(404, {'error': '未找到'})
            data = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self):
        if not self.authorized(): return
        if self.headers.get('X-Panel-Action') != '1' and self.path != '/api/report':
            return self.reply(403, {'error': '缺少操作标记'})
        try:
            data = self.body()
            with LOCK:
                if self.path == '/api/report':
                    status = {'ip': str(data.get('ip', '')), 'details': data.get('details', []),
                              'log': str(data.get('log', ''))[-24000:], 'updated_at': int(time.time())}
                    write_json(STATUS, status)
                    return self.reply(200, {'ok': True})
                settings = read_json(SETTINGS)
                if self.path == '/api/switch':
                    settings['switch_revision'] = int(time.time() * 1000)
                    write_json(SETTINGS, settings)
                    return self.reply(200, {'ok': True})
                if self.path != '/api/config':
                    return self.reply(404, {'error': '未找到'})
                country = str(data.get('country', '')).upper()
                port = data.get('proxy_port')
                mode = data.get('mode')
                manual = str(data.get('manual_node_ip', '')).strip()
                if len(country) != 2 or not country.isalpha() or not country.isascii():
                    raise ValueError('国家代码必须是两个英文字母')
                if type(port) is not int or not 1024 <= port <= 65535:
                    raise ValueError('端口必须在 1024–65535 之间')
                if mode not in ('auto', 'manual'):
                    raise ValueError('出口选择无效')
                if mode == 'manual':
                    ipaddress.IPv4Address(manual)
                elif manual:
                    ipaddress.IPv4Address(manual)
                new = dict(settings, country=country, proxy_port=port, mode=mode, manual_node_ip=manual)
                if port != settings['proxy_port']:
                    if port == settings['panel_port']:
                        raise ValueError('该端口已被管理面板使用')
                    with socket.socket() as test:
                        test.bind(('127.0.0.1', port))
                    config = read_json(SINGBOX_CONFIG)
                    old_config = json.loads(json.dumps(config))
                    next(item for item in config['outbounds'] if item['tag'] == 'home')['server_port'] = port
                    write_json(SINGBOX_CONFIG, config)
                    binary = settings.get('sing_box_bin', '/opt/residential-stack-bin/sing-box')
                    checked = subprocess.run([binary, 'check', '-c', str(SINGBOX_CONFIG)], capture_output=True, text=True, timeout=15)
                    if checked.returncode:
                        write_json(SINGBOX_CONFIG, old_config)
                        raise RuntimeError('sing-box 配置检查失败：' + checked.stderr[-300:])
                    try:
                        write_json(SETTINGS, new)
                        subprocess.run(['systemctl', 'restart', 'residential-agent.service'], check=True, timeout=20)
                        subprocess.run(['systemctl', 'restart', 'three-nodes.service'], check=True, timeout=20)
                    except Exception:
                        write_json(SETTINGS, settings)
                        write_json(SINGBOX_CONFIG, old_config)
                        subprocess.run(['systemctl', 'restart', 'residential-agent.service'], timeout=20)
                        subprocess.run(['systemctl', 'restart', 'three-nodes.service'], timeout=20)
                        raise
                else:
                    write_json(SETTINGS, new)
                return self.reply(200, {'ok': True, 'config': config_for_agent(new)})
        except Exception as exc:
            return self.reply(400, {'error': str(exc)})


def main():
    settings = read_json(SETTINGS)
    server = ThreadingHTTPServer(('0.0.0.0', settings['panel_port']), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ROOT / 'panel-cert.pem'), str(ROOT / 'panel-key.pem'))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
