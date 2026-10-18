import base64
import importlib.util
import json
import pathlib
import socket
import subprocess
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('panel_under_test', ROOT / 'residential-panel.py')
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    panel.ROOT = root
    panel.SETTINGS = root / 'settings.json'
    panel.AUTH = root / 'auth.json'
    panel.STATUS = root / 'status.json'
    panel.SINGBOX_CONFIG = root / 'three-nodes.json'
    panel.write_json(panel.AUTH, {'username': 'admin', 'password': 'secret'})
    panel.write_json(panel.SETTINGS, {'panel_port': 23456, 'proxy_port': 23457, 'country': 'US', 'mode': 'auto', 'manual_node_ip': '', 'switch_revision': 0})
    panel.write_json(panel.SINGBOX_CONFIG, {'outbounds': [{'type': 'socks', 'tag': 'home', 'server_port': 23457}]})
    server = panel.ThreadingHTTPServer(('127.0.0.1', 0), panel.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    auth = 'Basic ' + base64.b64encode(b'admin:secret').decode()

    def call(path, method='GET', data=None, authenticated=True):
        headers = {'Content-Type': 'application/json', 'X-Panel-Action': '1'}
        if authenticated:
            headers['Authorization'] = auth
        req = urllib.request.Request(url + path, headers=headers, method=method,
                                     data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.loads(response.read())

    try:
        call('/api/config', authenticated=False)
        raise AssertionError('unauthorized access was allowed')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    assert call('/api/config')['slot_map']['0'] == 'US'
    call('/api/report', 'POST', {'ip': '198.51.100.1', 'details': [{'country': 'US', 'node_ip': '198.51.100.2', 'port':23457}],
         'candidates':[{'ip':'198.51.100.3','country':'JP','ping':15}], 'log': 'healthy'})
    assert call('/api/status')['details'][0]['node_ip'] == '198.51.100.2'
    nodes = call('/api/nodes')
    assert json.loads(nodes[0]['details'])[0]['node_ip'] == '198.51.100.2'
    assert nodes[0]['candidates'][0]['ip'] == '198.51.100.3'
    assert 'JP' in call('/api/countries')
    call('/api/config', 'POST', {'slot_map': {'0':'JP'}, 'proxy_port': 23457, 'mode': 'auto', 'manual_node_ip': '',
         'youtube_check':True,'config_fetch_interval':20,'heartbeat_interval':40,'frontend_poll_interval':10})
    assert call('/api/config')['slot_map']['0'] == 'JP'
    assert call('/api/config')['youtube_check'] is True
    assert call('/api/config')['heartbeat_interval'] == 40
    page = urllib.request.urlopen(urllib.request.Request(url+'/',headers={'Authorization':auth})).read().decode()
    assert '住宅代理调度中心' in page and 'countries-list' in page and 'config-form' in page and 'remote-log' in page
    assert '__PANEL_USER__' not in page and '__PANEL_URL__' not in page
    call('/api/switch', 'POST', {})
    assert call('/api/config')['force_switch']['0'] > 0
    with patch.object(panel.subprocess, 'run') as runner:
        runner.return_value.returncode = 0
        call('/api/config', 'POST', {'country': 'JP', 'proxy_port': 23458, 'mode': 'auto', 'manual_node_ip': ''})
        assert runner.call_count == 3
    assert panel.read_json(panel.SINGBOX_CONFIG)['outbounds'][0]['server_port'] == 23458
    assert panel.read_json(panel.SETTINGS)['proxy_port'] == 23458
    panel.write_json(root / 'ws.json', {'outbounds': [{'type': 'socks', 'tag': 'home', 'server_port': 23458}]})
    with patch.object(panel.subprocess, 'run') as runner:
        runner.return_value.returncode = 0
        call('/api/config', 'POST', {'country': 'JP', 'proxy_port': 23459, 'mode': 'auto', 'manual_node_ip': ''})
        assert runner.call_count == 5
    assert panel.read_json(root / 'ws.json')['outbounds'][0]['server_port'] == 23459
    def failed_restart(args, **kwargs):
        if 'residential-ws.service' in args and kwargs.get('check'):
            raise subprocess.CalledProcessError(1, ['systemctl'])
        return subprocess.CompletedProcess(args, 0, '', '')
    with patch.object(panel.subprocess, 'run', side_effect=failed_restart):
        try:
            call('/api/config', 'POST', {'country': 'JP', 'proxy_port': 23460, 'mode': 'auto', 'manual_node_ip': ''})
            raise AssertionError('service failure was hidden')
        except urllib.error.HTTPError as error: assert error.code == 400
    assert panel.read_json(root / 'ws.json')['outbounds'][0]['server_port'] == 23459
    assert panel.read_json(panel.SINGBOX_CONFIG)['outbounds'][0]['server_port'] == 23459
    assert panel.read_json(panel.SETTINGS)['proxy_port'] == 23459
    server.shutdown()
print('panel authentication, reports, country, rotation, and port synchronization: OK')
