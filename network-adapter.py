#!/usr/bin/env python3
"""Independent host networking, Cloudflare ingress, certificates and verified exports.

See docs/NETWORK.md for the state schema and failure rules. Machine secrets stay
on the VPS; private-account API credentials remain at the hub. No other VPS relay.
"""
import argparse
import base64
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path('/etc/residential-stack')
BIN = pathlib.Path('/opt/residential-stack-bin')
UNITS = pathlib.Path('/etc/systemd/system')
API = 'https://api.cloudflare.com/client/v4'
WS_DESCRIPTION = 'Independent residential WebSocket entries'
TUNNEL_DESCRIPTION = 'Independent residential Cloudflare tunnel'


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else ({} if default is None else default)


def write(path, value):
    """Atomic private JSON. Reject symlinks rather than following unexpected paths."""
    if path.is_symlink() or path.parent.is_symlink(): raise ValueError('配置路径为符号链接，已停止')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.write('\n')
        os.chmod(name, 0o600); os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def run(args, check=True, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # argv can contain credentials; never echo it or raw tool output.
        raise RuntimeError(pathlib.Path(args[0]).name + ' 执行失败，请查看对应服务日志')
    return result


def ask(label, default='', secret=False):
    if os.environ.get('IP_SETUP_MODE', 'auto') == 'auto': return default
    with open('/dev/tty', 'r+', encoding='utf-8') as tty:
        if secret: return getpass.getpass(label + '：', stream=tty).strip() or default
        tty.write(label + ('（回车使用 ' + str(default) + '）' if default else '（可留空）') + '：'); tty.flush()
        return tty.readline().strip() or default


def mapped_hint():
    """Link-local default source strongly identifies Air, unlike ordinary cloud NAT."""
    try:
        route = json.loads(run(['ip', '-j', 'route', 'get', '1.1.1.1']).stdout)[0]
        return ipaddress.ip_address(route.get('prefsrc', '')).is_link_local
    except (OSError, ValueError, IndexError, subprocess.SubprocessError): return False


def prepare():
    if ROOT.exists() and not (ROOT / 'network.json').exists():
        panel = ROOT / 'panel.py'
        if any(ROOT.iterdir()) and (not panel.is_file() or 'ResidentialPanel/1.0' not in panel.read_text(encoding='utf-8')):
            raise ValueError('住宅目录归属无法确认，未写入网络配置')
    p = read(ROOT / 'network.json')
    if not p:
        mode = os.environ.get('IP_NETWORK_MODE') or ('mapped' if mapped_hint() else 'direct')
        choice = ask('网络类型：1 普通公网，2 端口映射', '2' if mode == 'mapped' else '1')
        mode = 'mapped' if choice == '2' else 'direct'
        p = {'schema': 1, 'instance': secrets.token_hex(6), 'mode': mode,
             'udp_available': mode == 'direct', 'mappings': {}}
    elif os.environ.get('IP_NETWORK_MODE') or os.environ.get('IP_SETUP_MODE') == 'manual':
        mode = os.environ.get('IP_NETWORK_MODE') or ('mapped' if ask('网络类型：1 普通公网，2 端口映射', '2' if p['mode'] == 'mapped' else '1') == '2' else 'direct')
        if mode not in ('mapped', 'direct'): raise ValueError('网络类型无效')
        if mode != p['mode']: p.update(mode=mode, udp_available=mode == 'direct')
    if os.environ.get('IP_UDP_AVAILABLE') in ('0', '1'):
        p['udp_available'] = os.environ['IP_UDP_AVAILABLE'] == '1'
    write(ROOT / 'network.json', p)
    print(p['mode']); print('0' if p['udp_available'] else '1')


class Cloudflare:
    def __init__(self, token): self.token = token

    def call(self, method, path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(API + path, data=payload, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=25) as response: data = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError('Cloudflare 请求失败，HTTP ' + str(error.code)) from None
        if not data.get('success'): raise RuntimeError('Cloudflare 未接受请求，请检查令牌权限和账号')
        return data.get('result')


def cf_credentials():
    c = read(ROOT / 'cloudflare.json')
    token = os.environ.get('IP_CF_API_TOKEN') or c.get('api_token') or ask('Cloudflare API 令牌', secret=True)
    if not token: raise ValueError('没有收到私人汇总授权，也未配置自己的 Cloudflare 授权。私人用户请登录汇总面板，点击“新增机器”重新复制完整专属命令；公共命令不会使用维护者的域名或隧道。自有资源模式可设置 IP_CF_API_TOKEN 和 IP_CF_ZONE。')
    cf = Cloudflare(token)
    zone_name = os.environ.get('IP_CF_ZONE') or c.get('zone_name') or ask('用于部署的 Cloudflare 域名')
    if not zone_name: raise ValueError('缺少域名，请设置 IP_CF_ZONE 或在手动模式填写')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', zone_name): raise ValueError('域名格式无效')
    zones = cf.call('GET', '/zones?name=' + urllib.parse.quote(zone_name))
    if len(zones) != 1 or zones[0].get('status') != 'active': raise ValueError('域名未启用或令牌无法读取此域名')
    c.update(api_token=token, zone_name=zones[0]['name'], zone_id=zones[0]['id'], account_id=zones[0]['account']['id'])
    write(ROOT / 'cloudflare.json', c)
    return cf, c


def ensure_dns(cf, c, state, name, kind, content, proxied):
    records = cf.call('GET', '/zones/' + c['zone_id'] + '/dns_records?name=' + urllib.parse.quote(name))
    owned = state.setdefault('dns', {})
    record = next((r for r in records if r['id'] == owned.get(name, {}).get('id')), None)
    if records and not record: raise ValueError('域名已有其他记录，未覆盖：' + name)
    body = {'type': kind, 'name': name, 'content': content, 'proxied': proxied, 'ttl': 1,
            'comment': 'ip installer instance=' + state['instance']}
    if record:
        previous = owned[name]
        if any(record.get(k) != previous.get(k) for k in ('type', 'content', 'proxied')):
            raise ValueError('域名记录已被外部修改，未覆盖：' + name)
        if any(record.get(k) != body[k] for k in ('type', 'content', 'proxied')):
            record = cf.call('PUT', '/zones/' + c['zone_id'] + '/dns_records/' + record['id'], body)
    else:
        record = cf.call('POST', '/zones/' + c['zone_id'] + '/dns_records', body)
    owned[name] = {'id': record['id'], 'type': kind, 'content': content, 'proxied': proxied}
    write(ROOT / 'network.json', state)


def install_release_binary(project, asset_name, destination):
    if destination.is_file() and os.access(destination, os.X_OK): return
    request = urllib.request.Request('https://api.github.com/repos/' + project + '/releases/latest',
                                     headers={'User-Agent': 'ip-installer'})
    with urllib.request.urlopen(request, timeout=25) as r: release = json.load(r)
    asset = next(a for a in release['assets'] if a['name'] == asset_name)
    digest = asset.get('digest', '')
    if not digest.startswith('sha256:'): raise ValueError('官方发布缺少 SHA256，未安装未校验文件')
    with urllib.request.urlopen(asset['browser_download_url'], timeout=120) as r: data = r.read(100 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != digest[7:]: raise ValueError('下载文件 SHA256 校验失败')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix('.new')
    temporary.write_bytes(data); temporary.chmod(0o700); os.replace(temporary, destination)
    write(ROOT / 'cloudflared-release.json', {'tag': release['tag_name'], 'asset': asset_name, 'sha256': digest[7:]})


def ensure_unit(name, description, executable, arguments, writable=None):
    path = UNITS / name
    previous = path.read_text() if path.exists() else ''
    if path.is_symlink() or (previous and ('Description=' + description) not in previous.splitlines()):
        raise ValueError('同名服务归属不明，未覆盖：' + name)
    source = ('[Unit]\nDescription=' + description + '\nAfter=network-online.target\nWants=network-online.target\n'
              '[Service]\nType=simple\nExecStart=' + str(executable) + ' ' + arguments + '\n'
              'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\nProtectHome=yes\n'
              'PrivateTmp=yes\nProtectSystem=strict\nLimitNOFILE=65535\n'
              '[Install]\nWantedBy=multi-user.target\n')
    changed = source != previous
    if changed: path.write_text(source)
    run(['systemctl', 'daemon-reload']); run(['systemctl', 'enable', name])
    if changed or run(['systemctl', 'is-active', '--quiet', name], check=False).returncode:
        run(['systemctl', 'restart', name])


def available_port(used=()):
    while True:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); p = sock.getsockname()[1]
        if p not in used: return p


def ws_config(state, settings, auth):
    entries = state.setdefault('ws', {})
    ports = [e['port'] for e in entries.values()]
    for role in ('tcp', 'udp', 'home'):
        if role not in entries:
            port = available_port(ports); ports.append(port)
            entries[role] = {'port': port, 'path': '/' + secrets.token_hex(16),
                'credential': secrets.token_hex(24) if role == 'udp' else str(uuid.uuid4())}
    inbounds = []
    for role, e in entries.items():
        kind = 'trojan' if role == 'udp' else 'vless'
        inbounds.append({'type': kind, 'tag': 'ws-' + role, 'listen': '127.0.0.1', 'listen_port': e['port'],
            'users': [{'password' if kind == 'trojan' else 'uuid': e['credential']}],
            'transport': {'type': 'ws', 'path': e['path']}})
    return {'log': {'level': 'warn'}, 'inbounds': inbounds,
        'outbounds': [{'type': 'direct', 'tag': 'direct'}, {'type': 'socks', 'tag': 'home',
            'server': '127.0.0.1', 'server_port': settings['proxy_port'], 'version': '5',
            'username': auth['socks_username'], 'password': auth['socks_password']}],
        'route': {'rules': [{'inbound': ['ws-home'], 'outbound': 'home'}], 'final': 'direct'}}


def ws_uri(role, entry, host):
    kind = 'trojan' if role == 'udp' else 'vless'
    label = {'tcp': 'VLESS-WS-TLS-TCP', 'udp': 'Trojan-WS-TLS-TCP-alternative', 'home': 'Residential-VLESS-WS-TLS'}[role]
    query = {'security': 'tls', 'type': 'ws', 'host': host, 'sni': host, 'path': entry['path']}
    if kind == 'vless': query['encryption'] = 'none'
    return kind + '://' + entry['credential'] + '@' + host + ':443?' + urllib.parse.urlencode(query) + '#' + label


def ws_origin_setup(state, settings, auth, binary):
    config = ws_config(state, settings, auth)
    candidate = ROOT / 'ws.json.new'; write(candidate, config)
    run([binary, 'check', '-c', str(candidate)])
    current = ROOT / 'ws.json'
    changed = not current.exists() or current.read_bytes() != candidate.read_bytes()
    os.replace(candidate, current); write(ROOT / 'network.json', state)
    ensure_unit('residential-ws.service', WS_DESCRIPTION, binary, 'run -c /etc/residential-stack/ws.json')
    if changed: run(['systemctl', 'restart', 'residential-ws.service'])


def tunnel_setup(cf, c, state, settings, auth, binary):
    ws_origin_setup(state, settings, auth, binary)
    base = '/accounts/' + c['account_id'] + '/cfd_tunnel'
    if not state.get('tunnel_id'):
        t = cf.call('POST', base, {'name': 'ip-' + state['instance'], 'config_src': 'cloudflare'})
        state['tunnel_id'] = t['id']; write(ROOT / 'network.json', state)
    else:
        t = cf.call('GET', base + '/' + state['tunnel_id'])
        if t.get('name') != 'ip-' + state['instance']: raise ValueError('隧道归属不匹配，未修改')
    hosts = {r: r + '-' + state['instance'] + '.' + c['zone_name'] for r in ('panel', 'tcp', 'udp', 'home')}
    ingress = [{'hostname': hosts['panel'], 'service': 'http://127.0.0.1:' + str(settings['agent_port'])}]
    ingress += [{'hostname': hosts[r], 'service': 'http://127.0.0.1:' + str(state['ws'][r]['port'])} for r in ('tcp', 'udp', 'home')]
    cf.call('PUT', base + '/' + state['tunnel_id'] + '/configurations', {'config': {'ingress': ingress + [{'service': 'http_status:404'}]}})
    for host in hosts.values(): ensure_dns(cf, c, state, host, 'CNAME', state['tunnel_id'] + '.cfargotunnel.com', True)
    state['tunnel_hosts'] = hosts; write(ROOT / 'network.json', state)
    token = cf.call('GET', base + '/' + state['tunnel_id'] + '/token')
    start_tunnel(token)
    return hosts, {r: ws_uri(r, state['ws'][r], hosts[r]) for r in ('tcp', 'udp', 'home')}


def start_tunnel(token):
    token_file = ROOT / 'tunnel.token'
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: f.write(token)
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
    install_release_binary('cloudflare/cloudflared', 'cloudflared-linux-' + arch, BIN / 'cloudflared')
    ensure_unit('residential-tunnel.service', TUNNEL_DESCRIPTION, BIN / 'cloudflared',
                'tunnel --no-autoupdate run --token-file /etc/residential-stack/tunnel.token')


def private_tunnel_setup(state, settings, auth, binary):
    ws_origin_setup(state, settings, auth, binary)
    config = read(ROOT / 'hub.json')
    requested = os.environ.get('IP_HUB_URL')
    if requested and config.get('url') and requested.rstrip('/') != config['url'].rstrip('/'):
        raise ValueError('私人授权属于其他后台，未覆盖')
    config['url'] = requested or config.get('url')
    if not config.get('url'): raise ValueError('缺少私人资源后台地址')
    payload = {'username': auth['username'], 'password': auth['password'],
        'ports': {'panel': settings['agent_port'], **{r: state['ws'][r]['port'] for r in ('tcp', 'udp', 'home')}},
        'external_address': state.get('external_address') or (settings['public_ip'] if state['mode'] == 'direct' else ''),
        'panel_external_port': state.get('panel_external_port') or (settings['panel_port'] if state['mode'] == 'direct' else 0)}
    result = hub_request(config, 'POST', 'provision', payload, os.environ.get('IP_HUB_ENROLLMENT'))
    if result.get('machine_token'):
        config.update(machine_token=result['machine_token'], machine_id=result['machine_id'], private_resources=True)
        write(ROOT / 'hub.json', config)
        state['hub_registered'] = True; write(ROOT / 'network.json', state)
    if not result.get('ok'): raise ValueError('私人资源配置未完成，授权已保存，可直接重试')
    state['private_hosts'] = result['hosts']
    state['private_direct_host'] = result.get('direct_host', '')
    write(ROOT / 'network.json', state)
    start_tunnel(result['tunnel_token'])
    return result['hosts'], {r: ws_uri(r, state['ws'][r], result['hosts'][r]) for r in ('tcp', 'udp', 'home')}


def mapped_link(link, address, port):
    if not link or not address or not port: return ''
    u = urllib.parse.urlsplit(link)
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', address): raise ValueError('公网地址格式无效')
    if not 1 <= int(port) <= 65535: raise ValueError('映射端口无效')
    return urllib.parse.urlunsplit((u.scheme, u.netloc.rsplit('@', 1)[0] + '@' + address + ':' + str(port), u.path, u.query, u.fragment))


def load_verifier():
    spec = importlib.util.spec_from_file_location('ip_verifier', ROOT / 'verify-node.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def choose_verified(candidates, test):
    """Prefer direct. A rejected candidate is never silently exported as working."""
    selected, results = {}, []
    for role in ('tcp', 'udp', 'home'):
        for link in dict.fromkeys(candidates.get(role, [])):
            if not link: continue
            report = test(link); report['role'] = role; results.append(report)
            if report['ok']:
                selected[role] = link; break
    return selected, results


def generated_exports(candidates, selected, native_links=None):
    """Export configured addresses even before ports open; readiness stays separate."""
    return {role: selected.get(role) or next((uri for uri in candidates.get(role, []) if uri), '') or (native_links or {}).get(role, '')
            for role in ('tcp', 'udp', 'home')}


def concise_result(state, settings, auth, native, exported, selected, panels, pending_panels, hub_ok, port_report):
    """Show generated configuration and the next action once. Details live in logs."""
    roles = ('tcp', 'udp', 'home')
    names = {'tcp': 'TCP 节点', 'udp': '第二条节点', 'home': '住宅节点'}
    count = sum(bool(exported.get(r)) for r in roles)
    ready = len(selected) == 3 and panels and hub_ok
    lines = ['部署结果：' + ('三个节点检测通过。' if ready else '已生成 ' + str(count) + ' 个节点，仍有设置待完成。')]
    raw_mapping = [r for r in roles if state['mode'] == 'mapped' and exported.get(r) and
                   r not in selected and exported[r] == native.get(r) and
                   not (state.get('external_address') and state.get('mappings', {}).get(r))]
    if count:
        lines += ['', '节点链接（可一起复制导入）：', *[exported[r] for r in roles if exported.get(r)]]
    if raw_mapping:
        lines.append('上述原始链接的地址和端口需改为平台分配的外部地址、外部端口。')
    waiting = [names[r] for r in roles if exported.get(r) and r not in selected]
    if waiting: lines.append('状态：已生成，公网检测未通过（' + '、'.join(waiting) + '）。')
    if not exported.get('udp'):
        lines.append('第二条未生成：未确认 UDP 支持，也未配置可用的 TCP 替代入口。' if not state.get('udp_available') else '第二条未生成，请查看详细日志。')
    elif exported['udp'].startswith('trojan://'):
        lines.append('第二条为 Trojan TCP 替代，不是 Hysteria2。')
    panel = (panels or pending_panels or [''])[0]
    unresolved = 'https://' + (state.get('external_address') or settings['public_ip']) + ':____/  【端口待检测】' + (
        '' if state.get('external_address') else '（出口 IP 候选，入站地址需确认）')
    lines += ['', '住宅管理面板：' + (panel or unresolved),
              '面板用户名：' + auth['username'], '面板密码：' + auth['password']]
    if panel and not panels: lines.append('面板待验证，请检查端口和证书。')
    # Only the entries actually exported through native listeners need these
    # ports. Do not advertise unused native backups for working CF WS links.
    native_roles = [r for r in roles if native.get(r) and (exported.get(r) == native[r] or
                    state['mode'] == 'mapped' and state.get('external_address') and state.get('mappings', {}).get(r) and
                    exported.get(r) == mapped_link(native[r], state['external_address'], state['mappings'][r]))]
    cf_panels = {state.get(key, {}).get('panel') for key in ('private_hosts', 'tunnel_hosts')}
    panel_native = not panel or urllib.parse.urlsplit(panel).hostname not in cf_panels
    needed = []
    for r in native_roles:
        uri = urllib.parse.urlsplit(native[r])
        proto = 'UDP' if uri.scheme in ('hysteria2', 'hy2') else 'TCP'
        needed.append((proto, uri.port, names[r]))
    if panel_native: needed.append(('TCP', settings['panel_port'], '住宅面板'))
    if needed:
        if ready:
            lines += ['', '下一步：复制节点导入客户端。',
                      '如外部连接失败，检查平台' + ('端口映射：' if state['mode'] == 'mapped' else '入站放行：') +
                      '、'.join(dict.fromkeys(proto + ' ' + str(port) for proto, port, _ in needed)) + '。']
        elif state['mode'] == 'mapped':
            lines += ['', '下一步：在平台后台确认以下端口映射：']
            lines += [proto + ' ' + str(port) + '（' + name + '）' for proto, port, name in needed]
            lines.append('将外部地址和端口填入 ' + str(ROOT / 'port-mappings.json') + '，再重跑命令按回车即可自动更新。')
        else:
            lines += ['', '下一步：在云平台放行入站端口：' + '、'.join(dict.fromkeys(proto + ' ' + str(port) for proto, port, _ in needed)) + '。']
    elif ready:
        lines += ['', '下一步：复制节点导入客户端；无需新增公网入站映射。']
    else:
        lines += ['', '下一步：检查隧道连接，稍后重跑安装命令。']
    optional = []
    for r in roles:
        if native.get(r) and r not in native_roles:
            uri = urllib.parse.urlsplit(native[r])
            optional.append(('UDP' if uri.scheme in ('hysteria2', 'hy2') else 'TCP') + ' ' + str(uri.port))
    if not panel_native: optional.append('TCP ' + str(settings['panel_port']))
    if optional:
        lines.append('备用直连端口（可选，' + ('需在平台创建映射' if state['mode'] == 'mapped' else '需在云平台放行入站') + '）：' + '、'.join(dict.fromkeys(optional)) + '。')
    failed_host = any(not item.get('host_opened') for item in port_report if any(item.get('protocol') == proto.lower() and item.get('internal_port') == port for proto, port, _ in needed))
    if failed_host: lines.append('本机防火墙放行失败，还需检查上述端口的系统防火墙规则。')
    private = bool(os.environ.get('IP_HUB_URL') or read(ROOT / 'hub.json').get('url'))
    if private and not hub_ok:
        lines.append('汇总接入未完成：检查面板和隧道后重跑原命令。' if read(ROOT / 'hub.json').get('machine_token') else
                     '私人授权未完成：从汇总面板重新取得有效命令后重试。')
    elif state['mode'] == 'mapped' and not exported.get('udp'):
        lines.append('需要第二条 TCP 替代入口：重跑命令选 2，填写自己的 CF 域名和令牌。')
    log = os.environ.get('IP_INSTALL_LOG') or str(ROOT / 'deployment-details.txt')
    lines += ['', '详细日志：' + log]
    return '\n'.join(lines) + '\n'


def pending_panel_urls(state, settings):
    """Never turn an unknown NAT mapping into a fabricated public URL."""
    urls = []
    if state['mode'] == 'direct':
        urls.append('https://' + settings['public_ip'] + ':' + str(settings['panel_port']) + '/')
    elif state.get('external_address') and state.get('panel_external_port'):
        urls.append('https://' + state['external_address'] + ':' + str(state['panel_external_port']) + '/')
    if state.get('private_hosts', {}).get('panel'):
        urls.append('https://' + state['private_hosts']['panel'] + '/')
    if state.get('tunnel_hosts', {}).get('panel'):
        urls.append('https://' + state['tunnel_hosts']['panel'] + '/')
    return list(dict.fromkeys(urls))


def access_instructions(state, settings, links):
    lines = []
    for role, title in [('tcp', 'TCP 节点'), ('udp', 'Hysteria2 UDP'), ('home', '住宅节点')]:
        uri = links.get(role)
        if uri:
            proto = 'UDP' if urllib.parse.urlsplit(uri).scheme in ('hysteria2', 'hy2') else 'TCP'
            inside = urllib.parse.urlsplit(uri).port
            if state['mode'] == 'mapped':
                outside = state.get('mappings', {}).get(role)
                lines.append(title + '：' + proto + ' 内部端口 ' + str(inside) +
                    (' → 外部端口 ' + str(outside) if outside else ' → 在平台创建映射后记录外部端口'))
            else: lines.append(title + '：云平台入站放行 ' + proto + ' ' + str(inside))
    inside = settings['panel_port']
    lines.append('住宅面板：TCP 内部端口 ' + str(inside) +
        (' → ' + ('外部端口 ' + str(state['panel_external_port']) if state.get('panel_external_port') else '在平台创建映射后记录外部端口')
         if state['mode'] == 'mapped' else '；云平台入站放行 TCP ' + str(inside)))
    if state['mode'] == 'mapped':
        lines += ['映射机器的公网出口 IP 不一定是入站地址；请使用平台给出的外部主机名和外部端口。',
                  '映射后再次运行安装命令，选择 2 手动填写外部地址和各端口；会复用已有节点和密码。']
        if not state.get('udp_available'):
            lines.append('尚未确认平台支持 UDP：不能把 Other/TCP 映射当作 Hysteria2。第二条的 TCP 替代入口需要同机 CF 隧道授权。')
    lines += ['住宅代理内部端口 ' + str(settings.get('proxy_port', '见 settings.json')) + '、内部 API 和 WS 后端不需要向公网开放。',
              '已授权且连接正常的 CF 隧道入口不需要新增公网入站映射。']
    return lines


def panel_check(url, auth):
    request = urllib.request.Request(url.rstrip('/') + '/api/config', headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ip-installer/1.0)',
        'Authorization': 'Basic ' + base64.b64encode((auth['username'] + ':' + auth['password']).encode()).decode()})
    try:
        with urllib.request.urlopen(request, timeout=12) as r:
            data = json.load(r)
        return data.get('slot_map') is not None
    except (OSError, ValueError, urllib.error.URLError): return False


def dns_panel_setup(cf, c, state, settings, private_host=None):
    """Backup requires a real mapped TCP entry. DNS alone cannot create an entry."""
    outside = state.get('panel_external_port')
    address = state.get('external_address')
    if not outside or not address: raise ValueError('备用面板缺少外部地址或面板端口映射')
    host = private_host or 'direct-' + state['instance'] + '.' + c['zone_name']
    if not private_host:
        try: content = str(ipaddress.IPv4Address(address)); kind = 'A'
        except ValueError: content = address; kind = 'CNAME'
        ensure_dns(cf, c, state, host, kind, content, False)
    certbot = pathlib.Path('/opt/residential-certbot/bin/certbot')
    if not certbot.exists():
        if run(['python3', '-m', 'venv', '/opt/residential-certbot'], check=False).returncode:
            run(['apt-get', 'install', '-y', 'python3-venv'], timeout=180)
            run(['python3', '-m', 'venv', '/opt/residential-certbot'])
    run(['/opt/residential-certbot/bin/pip', '--disable-pip-version-check', 'install', 'certbot==5.4.0'] +
         ([] if private_host else ['certbot-dns-cloudflare==5.4.0']), timeout=180)
    if private_host:
        for action in ('auth', 'cleanup'):
            hook = ROOT / ('dns-' + action + '.sh')
            hook.write_text('#!/bin/sh\nexec /usr/bin/python3 /etc/residential-stack/network-adapter.py dns-' + action + '\n'); hook.chmod(0o700)
        plugin = ['--manual', '--preferred-challenges', 'dns', '--manual-auth-hook', str(ROOT / 'dns-auth.sh'),
                  '--manual-cleanup-hook', str(ROOT / 'dns-cleanup.sh')]
    else:
        credentials = ROOT / 'dns-credentials.ini'
        credentials.write_text('dns_cloudflare_api_token = ' + c['api_token'] + '\n'); credentials.chmod(0o600)
        plugin = ['--dns-cloudflare', '--dns-cloudflare-credentials', str(credentials), '--dns-cloudflare-propagation-seconds', '30']
    args = [str(certbot), 'certonly', *plugin, '-d', host, '--cert-name', 'residential-panel-domain',
            '--config-dir', str(ROOT / 'acme'), '--work-dir', str(ROOT / 'acme-work'),
            '--logs-dir', str(ROOT / 'acme-logs'), '--non-interactive', '--agree-tos',
            '--register-unsafely-without-email', '--keep-until-expiring']
    run(args, timeout=180)
    cert_root = ROOT / 'acme/live/residential-panel-domain'
    new = dict(settings, public_cert=str(cert_root / 'fullchain.pem'), public_key=str(cert_root / 'privkey.pem'))
    write(ROOT / 'settings.json', new)
    try: run(['systemctl', 'restart', 'residential-panel.service'])
    except (RuntimeError, OSError, subprocess.SubprocessError):
        write(ROOT / 'settings.json', settings)
        run(['systemctl', 'restart', 'residential-panel.service'], check=False)
        raise
    # Reuse the owned timer. The deploy hook only restarts this panel.
    hook = ROOT / 'apply-domain-cert.sh'
    hook.write_text('#!/bin/sh\nset -eu\nsystemctl restart residential-panel.service\n'); hook.chmod(0o700)
    renew = ROOT / 'renew-panel-cert.sh'
    renew.write_text('#!/bin/sh\nset -eu\n' + str(certbot) +
        ' renew --cert-name residential-panel-domain --non-interactive --no-random-sleep-on-renew'
        ' --config-dir /etc/residential-stack/acme --work-dir /etc/residential-stack/acme-work'
        ' --logs-dir /etc/residential-stack/acme-logs --deploy-hook /etc/residential-stack/apply-domain-cert.sh\n')
    renew.chmod(0o700)
    (UNITS / 'residential-cert-renew.service').write_text('[Unit]\nDescription=Renew trusted residential panel IP certificate\n[Service]\nType=oneshot\nExecStart=/etc/residential-stack/renew-panel-cert.sh\nTimeoutStartSec=240\n')
    (UNITS / 'residential-cert-renew.timer').write_text('[Unit]\nDescription=Check residential panel IP certificate twice daily\n[Timer]\nOnBootSec=10min\nOnUnitActiveSec=12h\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
    run(['systemctl', 'daemon-reload']); run(['systemctl', 'enable', '--now', 'residential-cert-renew.timer'])
    state['certificate_mode'] = 'domain'; write(ROOT / 'network.json', state)
    return 'https://' + host + ':' + str(outside) + '/'


def firewall(ports):
    """Only open named host ports; never flush existing rules or change policies."""
    results = []
    for proto, port in sorted(set(ports)):
        if not 1 <= int(port) <= 65535 or proto not in ('tcp', 'udp'): raise ValueError('防火墙端口无效')
        ok, backend = True, '无需修改'
        try:
            if shutil.which('ufw') and 'Status: active' in run(['ufw', 'status'], check=False).stdout:
                backend = 'UFW'; run(['ufw', 'allow', str(port) + '/' + proto])
            elif shutil.which('firewall-cmd') and run(['firewall-cmd', '--state'], check=False).returncode == 0:
                backend = 'firewalld'; run(['firewall-cmd', '--add-port=' + str(port) + '/' + proto])
                run(['firewall-cmd', '--permanent', '--add-port=' + str(port) + '/' + proto])
            elif shutil.which('iptables'):
                backend = 'iptables'
                rule = ['INPUT', '-p', proto, '--dport', str(port), '-m', 'comment', '--comment', 'ip-installer', '-j', 'ACCEPT']
                if run(['iptables', '-C', *rule], check=False).returncode: run(['iptables', '-I', *rule])
                if shutil.which('ip6tables'):
                    if run(['ip6tables', '-C', *rule], check=False).returncode: run(['ip6tables', '-I', *rule])
            else: ok = False; backend = '未找到支持的防火墙工具'
        except (OSError, RuntimeError, subprocess.SubprocessError): ok = False
        results.append({'protocol': proto, 'internal_port': int(port), 'host_opened': ok,
                        'backend': backend, 'cloud_verified': False})
    return results


def configure_mappings(state, links, settings, discovered=False):
    value = lambda label, default: default if discovered else ask(label, default)
    state['external_address'] = os.environ.get('IP_EXTERNAL_ADDRESS') or value('平台给出的外部主机名', state.get('external_address', ''))
    for role in ('tcp', 'udp', 'home'):
        original = links.get(role, '')
        if not original or (role == 'udp' and not state['udp_available']): continue
        inside = urllib.parse.urlsplit(original).port
        number = os.environ.get('IP_MAP_' + role.upper()) or value(
            ('UDP' if role == 'udp' else 'TCP') + ' 内部端口 ' + str(inside) + ' 对应的外部端口（0 跳过直连）', str(state['mappings'].get(role, 0)))
        if not str(number).isdigit() or not 0 <= int(number) <= 65535: raise ValueError('外部端口无效')
        state['mappings'][role] = int(number)
    state['panel_external_port'] = int(os.environ.get('IP_MAP_PANEL') or value(
        '面板内部端口 ' + str(settings['panel_port']) + ' 对应的外部端口（0 跳过备用）', str(state.get('panel_external_port', 0))))
    if not 0 <= state['panel_external_port'] <= 65535: raise ValueError('面板外部端口无效')
    write(ROOT / 'network.json', state)


def load_mapping_table(state, links, settings):
    """Provider-neutral handoff: import only mappings of this stack's listeners."""
    path = ROOT / 'port-mappings.json'
    roles = {r: urllib.parse.urlsplit(uri).port for r, uri in links.items() if uri and r in ('tcp', 'home', 'udp')}
    roles['panel'] = settings['panel_port']
    if not path.exists():
        table = {'external_address': state.get('external_address', ''), 'tcp': {}, 'udp': {}}
        for role, inside in roles.items():
            protocol = 'udp' if role == 'udp' else 'tcp'
            table[protocol][str(inside)] = state.get('panel_external_port', 0) if role == 'panel' else state.get('mappings', {}).get(role, 0)
        write(path, table)
    if path.is_symlink(): raise ValueError('映射信息文件是符号链接，未读取')
    try: table = read(path)
    except (ValueError, TypeError): raise ValueError('port-mappings.json 不是有效 JSON，请填写外部地址和端口') from None
    if not isinstance(table, dict): raise ValueError('port-mappings.json 必须是 JSON 对象')
    address = table.get('external_address', '')
    if address and (not isinstance(address, str) or not re.fullmatch(r'[a-zA-Z0-9.-]+', address)):
        raise ValueError('port-mappings.json 的外部地址无效')
    pending = {}
    for role, inside in roles.items():
        protocol = 'udp' if role == 'udp' else 'tcp'
        mapping = table.get(protocol, {})
        if not isinstance(mapping, dict): raise ValueError('port-mappings.json 的 tcp/udp 必须是内部端口到外部端口的表')
        outside = mapping.get(str(inside), 0)
        if type(outside) is not int or not 0 <= outside <= 65535: raise ValueError('port-mappings.json 的外部端口必须是 0 到 65535 的整数')
        if outside: pending[role] = outside
    if address: state['external_address'] = address
    for role, outside in pending.items():
        if role == 'panel': state['panel_external_port'] = outside
        else: state.setdefault('mappings', {})[role] = outside
    if address and pending: state['mapping_source'] = 'mapping-file'
    write(ROOT / 'network.json', state)
    return bool(address and pending)


def air_call(token, method, path, body=None):
    request = urllib.request.Request('https://api.dsx-air.nvidia.com/api/v3/' + path,
        method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0 (compatible; ip-installer/1.0)'})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response) if response.status != 204 else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError('NVIDIA 平台端口操作失败，HTTP ' + str(e.code)) from None


def air_local_mac():
    route = json.loads(run(['ip', '-j', 'route', 'get', '1.1.1.1']).stdout)[0]
    device = route.get('dev', '')
    if not device or not re.fullmatch(r'[a-zA-Z0-9_.:@-]+', device): raise ValueError('无法识别默认网卡，请配置 IP_AIR_INTERFACE_ID')
    interfaces = json.loads(run(['ip', '-j', 'link', 'show', 'dev', device]).stdout)
    mac = str(interfaces[0].get('address', '')).lower()
    if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', mac): raise ValueError('无法识别网卡 MAC，请配置 IP_AIR_INTERFACE_ID')
    return mac


def resolve_air_interface(token, saved):
    explicit = os.environ.get('IP_AIR_INTERFACE_ID') or (saved.get('interface') if saved.get('selection', 'explicit') == 'explicit' else '')
    if explicit: return explicit, {'selection': 'explicit'}
    mac = air_local_mac()
    found = air_call(token, 'GET', 'simulations/nodes/interfaces/?mac_address=' + urllib.parse.quote(mac)).get('results', [])
    matches = [item for item in found if str(item.get('mac_address', '')).lower() == mac]
    if len(matches) != 1: raise ValueError('平台网卡无法唯一匹配本机，请配置 IP_AIR_INTERFACE_ID；未改动平台服务')
    return matches[0]['id'], {'selection': 'mac', 'mac_address': mac}


def auto_air_mappings(state, links, settings):
    saved = read(ROOT / 'air.json')
    token = os.environ.get('IP_AIR_API_TOKEN') or saved.get('token')
    if not token: return False
    state.setdefault('mappings', {})
    write(ROOT / 'air.json', {**saved, 'token': token})
    interface, identity = resolve_air_interface(token, saved)
    if not re.fullmatch(r'[a-fA-F0-9-]{36}', interface): raise ValueError('NVIDIA 接口编号格式无效')
    write(ROOT / 'air.json', {'token': token, 'interface': interface, **identity})
    base = 'simulations/nodes/interfaces/services/'
    roles = {r: urllib.parse.urlsplit(links[r]).port for r in ('tcp', 'home') if links.get(r)}
    roles['panel'] = settings['panel_port']
    for role, inside in roles.items():
        existing = air_call(token, 'GET', base + '?interface=' + interface + '&node_port=' + str(inside)).get('results', [])
        matches = [s for s in existing if s.get('interface') == interface and s.get('node_port') == inside]
        if len(matches) > 1: raise ValueError('同一内部端口有多个平台映射，请先在平台确认；未修改这些服务')
        service = matches[0] if matches else None
        if not service:
            service = air_call(token, 'POST', base, {'interface': interface, 'node_port': inside,
                'name': 'ip-' + state['instance'] + '-' + role, 'service_type': 'HTTPS' if role == 'panel' else 'OTHER'})
            state.setdefault('air_services', {})[service['id']] = {'name': service['name'], 'node_port': inside, 'interface': interface}
            write(ROOT / 'network.json', state)
        if service.get('interface') != interface or service.get('node_port') != inside: raise ValueError('平台返回的映射不属于请求的本机端口')
        if service.get('worker_port') and service.get('worker_fqdn'):
            state['external_address'] = service['worker_fqdn']
            if role == 'panel': state['panel_external_port'] = service['worker_port']
            else: state['mappings'][role] = service['worker_port']
        elif role == 'panel': state['panel_external_port'] = 0
        else: state['mappings'][role] = 0
    state['mapping_source'] = 'nvidia-api'
    state['mappings_updated_at'] = int(time.time())
    write(ROOT / 'network.json', state)
    return True


def apply():
    state, settings, auth = read(ROOT / 'network.json'), read(ROOT / 'settings.json'), read(ROOT / 'auth.json')
    links = read(ROOT / 'native-links.json', read(ROOT / 'links.json'))
    if not settings or not auth: raise ValueError('独立住宅面板未部署完成，无法继续网络适配')
    binary = settings.get('sing_box_bin', str(BIN / 'sing-box'))
    if not pathlib.Path(binary).is_file(): raise ValueError('没有找到节点程序')
    candidates = {role: [links.get(role, '')] for role in ('tcp', 'udp', 'home')}
    errors, panel_urls = [], []
    if state['mode'] == 'mapped':
        discovered = load_mapping_table(state, links, settings)
        try: discovered = auto_air_mappings(state, links, settings) or discovered
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
            errors.append(safe_error(exc))
        configure_mappings(state, links, settings, discovered=discovered)
        candidates = {role: [mapped_link(links.get(role), state.get('external_address'), state['mappings'].get(role))]
                      for role in ('tcp', 'udp', 'home')}
        if not state['udp_available']: candidates['udp'] = []
    ports = []
    for role in ('tcp', 'udp', 'home'):
        if role == 'udp' and not state['udp_available']: continue
        if links.get(role): ports.append(('udp' if role == 'udp' else 'tcp', urllib.parse.urlsplit(links[role]).port))
    # Open/list the panel listener even when its cloud-side mapping is created later.
    ports.append(('tcp', settings['panel_port']))
    port_report = firewall(ports)
    write(ROOT / 'firewall-ports.json', ports)
    firewall_unit = UNITS / 'residential-firewall.service'
    firewall_text = ('[Unit]\nDescription=Open only ip installer host ports\nAfter=network-pre.target\nBefore=three-nodes.service\n'
        '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
        'ExecStart=/usr/bin/python3 /etc/residential-stack/network-adapter.py firewall\n'
        '[Install]\nWantedBy=multi-user.target\n')
    if firewall_unit.is_symlink() or (firewall_unit.exists() and 'Description=Open only ip installer host ports' not in firewall_unit.read_text().splitlines()):
        raise ValueError('端口服务归属无法确认，未覆盖')
    firewall_unit.write_text(firewall_text)
    run(['systemctl', 'daemon-reload']); run(['systemctl', 'enable', 'residential-firewall.service'])
    if state['mode'] == 'mapped':
        # No repeated public-IP HTTP-01 attempts on an unmapped machine.
        if state.get('certificate_mode') != 'domain' and (UNITS / 'residential-cert-renew.timer').exists():
            run(['systemctl', 'disable', '--now', 'residential-cert-renew.timer'], check=False)
    else:
        if settings.get('panel_url') and panel_check(settings['panel_url'], auth): panel_urls.append(settings['panel_url'])
        ip_url = 'https://' + settings['public_ip'] + ':' + str(settings['panel_port']) + '/'
        if not panel_urls and panel_check(ip_url, auth): panel_urls.append(ip_url)
    os.environ.setdefault('IP_HY2_CERT', '/etc/sing-box/hy2-cert.pem')
    verifier = load_verifier()
    checked = {}
    def test(link):
        if link not in checked: checked[link] = verifier.verify(link, binary)
        return dict(checked[link])
    print('\n正在检查节点，请稍候……', flush=True)
    native_good = {role: any(link and test(link)['ok'] for link in candidates[role]) for role in candidates}
    private = bool(os.environ.get('IP_HUB_URL') or read(ROOT / 'hub.json').get('url')) and os.environ.get('IP_PRIVATE_RESOURCES', '1') != '0'
    # The private hub accepts only its authorized HTTPS hostnames, never a raw IP
    # or arbitrary origin. Keep native node priority while provisioning its panel.
    need_cf = private or state['mode'] == 'mapped' or not panel_urls or not all(native_good.values())
    if need_cf:
        try:
            if private: panel_urls = []
            cf, c = (None, {}) if private else cf_credentials()
            try:
                hosts, alternatives = private_tunnel_setup(state, settings, auth, binary) if private else tunnel_setup(cf, c, state, settings, auth, binary)
                for role in candidates: candidates[role].append(alternatives[role])
                url = 'https://' + hosts['panel'] + '/'
                for attempt in range(4):
                    if panel_check(url, auth): panel_urls.append(url); break
                    if attempt < 3: time.sleep(5)
                if not panel_urls: errors.append('隧道面板尚未通过 HTTPS 检测，可能在等待 DNS 或边缘证书生效')
            except (OSError, RuntimeError, ValueError, StopIteration, urllib.error.URLError, subprocess.SubprocessError) as e:
                errors.append('隧道部署失败：' + safe_error(e))
            if not panel_urls:
                try:
                    if state['mode'] == 'direct':
                        state['external_address'] = settings['public_ip']; state['panel_external_port'] = settings['panel_port']
                    private_host = state.get('private_direct_host') if private else None
                    if private and not private_host: raise ValueError('私人备用面板尚无已授权的 DNS 记录或端口映射')
                    url = dns_panel_setup(cf, c, state, settings, private_host)
                    if panel_check(url, auth): panel_urls.append(url)
                    else: errors.append('域名备用面板未通过公开 HTTPS 检测，请检查端口映射')
                except (OSError, RuntimeError, ValueError, urllib.error.URLError, subprocess.SubprocessError) as e:
                    errors.append('备用证书入口失败：' + safe_error(e))
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as e:
            errors.append(safe_error(e))
    # Domain/credentials can already exist when the connector or TLS readiness
    # check fails. Keep these generated entries visible, without marking ready.
    assigned_hosts = state.get('private_hosts') or state.get('tunnel_hosts') or {}
    for role in ('tcp', 'udp', 'home'):
        if assigned_hosts.get(role) and state.get('ws', {}).get(role):
            uri = ws_uri(role, state['ws'][role], assigned_hosts[role])
            if uri not in candidates[role]: candidates[role].append(uri)
    selected, checks = choose_verified(candidates, test)
    exported = generated_exports(candidates, selected, links)
    pending_panels = [u for u in pending_panel_urls(state, settings) if u not in panel_urls]
    report = {'schema': 1, 'mode': state['mode'], 'checks': checks, 'ports': port_report,
              'errors': errors, 'panel_urls': panel_urls, 'ready_roles': list(selected),
              'scope': 'public-self-test', 'client_test_required': True,
              'generated_roles': [r for r, uri in exported.items() if uri], 'pending_panel_urls': pending_panels}
    write(ROOT / 'network-report.json', report)
    write(ROOT / 'candidate-links.json', candidates)
    write(ROOT / 'links.json', {r: selected.get(r, '') for r in ('tcp', 'udp', 'home')})
    hub_ok = not bool(os.environ.get('IP_HUB_URL') or read(ROOT / 'hub.json').get('url'))
    if panel_urls:
        new = read(ROOT / 'settings.json'); new['panel_url'] = panel_urls[0]; write(ROOT / 'settings.json', new)
        try:
            hub_url = register_hub(panel_urls[0], auth, state)
            if hub_url: errors.append('已登记到统一管理入口：' + hub_url); hub_ok = True
        except (ValueError, RuntimeError, OSError, urllib.error.URLError) as error:
            errors.append(safe_error(error))
    report['hub_registered'] = bool(state.get('hub_registered'))
    report['hub_requirement_met'] = hub_ok
    write(ROOT / 'network-report.json', report)
    export = '\n'.join(exported[r] for r in ('tcp', 'udp', 'home') if exported[r])
    destination = pathlib.Path('/root/three-nodes-links.txt')
    destination.write_text(export + ('\n' if export else '')); destination.chmod(0o600)
    lines = ['部署结果：' + ('三个入口公开自测通过' if len(selected) == 3 and panel_urls and hub_ok else '部分项目尚未通过'),
             '已生成的节点链接（可一起复制导入；未通过项需先放行端口或修复连接）：', '', export or '暂无可确认外部地址的链接；原生节点配置仍保存在本机，见下方映射或授权提示。', '',
             '检查范围：这台 VPS 访问公开入口；尚未代表你所在网络的 v2rayN 实测。']
    for role, title in [('tcp', '第一条'), ('udp', '第二条'), ('home', '住宅出口')]:
        lines.append(title + '：' + ('公开握手和连续两次 HTTPS 出站通过' if role in selected else
            '已生成，公网检测未通过；可先开放端口再重试' if exported[role] else
            '尚缺外部地址/端口映射或已授权的替代入口，未编造公网链接'))
    if exported['udp'].startswith('trojan://'):
        lines.append('第二条使用 Trojan WebSocket + TLS，属于 TCP 替代方案，不是 Hysteria2。')
    lines += ['住宅管理面板：' + ('、'.join(panel_urls) if panel_urls else '公网入口尚未验证通过'),
              *(['已生成的面板地址（待端口开放及证书验证）：' + '、'.join(pending_panels)] if pending_panels else []),
              '面板本机地址：https://127.0.0.1:' + str(settings['panel_port']) + '/（仅本机或 SSH 转发访问；临时证书可能不受信任）',
              '面板用户名：' + auth['username'], '面板密码：' + auth['password'], '',
              '后台端口提示（云平台未验证时仍需核对）：']
    for item in port_report:
        lines.append(item['protocol'].upper() + ' 内部端口 ' + str(item['internal_port']) +
                     '；系统放行' + ('完成' if item['host_opened'] else '失败，请手动检查') + '；云平台需放行或建立映射')
    lines += access_instructions(state, settings, links)
    lines += errors
    lines += ['再次检查：可重新运行安装命令；已有配置会复用，不需要先卸载。',
              '详细检测：/etc/residential-stack/network-report.json',
              '未通过的候选链接：/etc/residential-stack/candidate-links.json（仅管理员可读）']
    summary = '\n'.join(lines) + '\n'
    detail = ROOT / 'deployment-details.txt'; detail.write_text(summary, encoding='utf-8'); detail.chmod(0o600)
    install_log = os.environ.get('IP_INSTALL_LOG')
    if install_log:
        with open(install_log, 'a', encoding='utf-8') as stream: stream.write('\n' + summary)
    concise = concise_result(state, settings, auth, links, exported, selected, panel_urls, pending_panels, hub_ok, port_report)
    info = pathlib.Path('/root/three-nodes-info.txt'); info.write_text(concise, encoding='utf-8'); info.chmod(0o600)
    print('\n' + concise)
    return 0 if len(selected) == 3 and panel_urls and hub_ok else 2


def safe_error(error):
    return str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__


def hub_request(config, method, action, payload=None, enrollment=None):
    origin = urllib.parse.urlsplit(config['url'])
    if origin.scheme != 'https' or origin.username or origin.password or origin.path not in ('', '/') or origin.query or origin.fragment:
        raise ValueError('统一面板地址必须是 HTTPS 网站根地址')
    token = config.get('machine_token') or enrollment
    if not token: raise ValueError('缺少管理员签发的私人授权；不会自动获取他人的域名或隧道')
    request = urllib.request.Request(config['url'].rstrip('/') + '/api/' + action, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0 (compatible; ip-installer/1.0)'})
    try:
        with urllib.request.urlopen(request, timeout=90 if action == 'provision' else 25) as response: return json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read(8192).decode('utf-8', errors='replace')
        if '1010' in raw or '<html' in raw.lower() or '<!doctype html' in raw.lower():
            raise RuntimeError('统一面板请求被 Cloudflare 边缘拦截，HTTP ' + str(error.code) + '；不是端口未开放，也不能据此判定授权码过期。请使用最新版安装命令重试。') from None
        if error.code == 403:
            detail = '本机授权可能已撤销，请保留配置并在汇总后台核对' if config.get('machine_token') else '私人授权码无效、已过期或已使用；请在汇总面板点击“新增机器”获取新命令'
            raise RuntimeError('统一面板授权未通过，HTTP 403；' + detail) from None
        raise RuntimeError('统一面板接入失败，HTTP ' + str(error.code)) from None


def register_hub(panel_url, auth, state):
    config = read(ROOT / 'hub.json')
    requested = os.environ.get('IP_HUB_URL')
    if requested and config.get('url') and requested.rstrip('/') != config['url'].rstrip('/'):
        raise ValueError('已有统一面板授权属于其他地址；请先解除原接入，未覆盖')
    config['url'] = requested or config.get('url')
    enrollment = os.environ.get('IP_HUB_ENROLLMENT')
    if not config['url']: return ''
    if not config.get('machine_token') and not enrollment:
        raise ValueError('接入私人汇总面板需要管理员签发的一次性接入码；普通仓库使用者不会自动加入')
    payload = {'panel_origin': panel_url, 'username': auth['username'], 'password': auth['password']}
    result = hub_request(config, 'POST', 'machine' if config.get('machine_token') else 'register', payload, enrollment)
    if result.get('machine_token'): config.update(machine_token=result['machine_token'], machine_id=result['machine_id'])
    write(ROOT / 'hub.json', config)
    state['hub_registered'] = True; write(ROOT / 'network.json', state)
    return config['url']


def cleanup_cloudflare():
    state, c = read(ROOT / 'network.json'), read(ROOT / 'cloudflare.json')
    hub = read(ROOT / 'hub.json')
    if state.get('hub_registered') and hub.get('machine_token'):
        hub_request(hub, 'DELETE', 'machine')
        state['hub_registered'] = False; write(ROOT / 'network.json', state)
    if state.get('air_services'):
        air = read(ROOT / 'air.json')
        if not air.get('token'): raise ValueError('缺少 NVIDIA 云端资源清理授权')
        base = 'simulations/nodes/interfaces/services/'
        for identifier, own in list(state['air_services'].items()):
            current = air_call(air['token'], 'GET', base + identifier + '/')
            if any(current.get(k) != own[k] for k in ('name', 'node_port', 'interface')):
                raise ValueError('NVIDIA 映射已被修改，未删除')
            air_call(air['token'], 'DELETE', base + identifier + '/')
            del state['air_services'][identifier]; write(ROOT / 'network.json', state)
    if not state.get('tunnel_id') and not state.get('dns'): return
    if not c.get('api_token'): raise ValueError('缺少清理本脚本云端资源的令牌，请保留配置后重试')
    cf = Cloudflare(c['api_token'])
    for name, own in list(state.get('dns', {}).items()):
        records = cf.call('GET', '/zones/' + c['zone_id'] + '/dns_records?name=' + urllib.parse.quote(name))
        current = next((r for r in records if r['id'] == own['id']), None)
        if current:
            if current.get('content') != own['content'] or current.get('type') != own['type']:
                raise ValueError('记录已被修改，未删除：' + name)
            cf.call('DELETE', '/zones/' + c['zone_id'] + '/dns_records/' + own['id'])
        del state['dns'][name]; write(ROOT / 'network.json', state)
    if state.get('tunnel_id'):
        base = '/accounts/' + c['account_id'] + '/cfd_tunnel/' + state['tunnel_id']
        current = cf.call('GET', base)
        if current.get('name') != 'ip-' + state['instance']: raise ValueError('隧道归属不匹配，未删除')
        cf.call('DELETE', base)
        del state['tunnel_id']; write(ROOT / 'network.json', state)


def main():
    global ROOT
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply', 'cleanup-cloudflare', 'firewall', 'dns-auth', 'dns-cleanup'])
    parser.add_argument('--state-dir', type=pathlib.Path, help='仅用于清理卸载时保留的授权目录')
    args = parser.parse_args()
    if os.geteuid() != 0: raise ValueError('请使用管理员权限运行')
    if args.state_dir:
        if args.action != 'cleanup-cloudflare': raise ValueError('自定义目录仅可用于云端资源清理')
        ROOT = args.state_dir.resolve()
    if args.action == 'prepare': prepare(); return 0
    if args.action in ('dns-auth', 'dns-cleanup'):
        validation = os.environ.get('CERTBOT_VALIDATION', '')
        hub_request(read(ROOT / 'hub.json'), 'POST', 'dns-challenge',
                    {'action': 'present' if args.action == 'dns-auth' else 'cleanup', 'value': validation})
        if args.action == 'dns-auth': time.sleep(30)
        return 0
    if args.action == 'firewall':
        return 0 if all(r['host_opened'] for r in firewall(read(ROOT / 'firewall-ports.json', []))) else 2
    if args.action == 'cleanup-cloudflare': cleanup_cloudflare(); return 0
    return apply()


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, urllib.error.URLError, subprocess.SubprocessError) as exc:
        print('网络适配未完成：' + safe_error(exc), file=sys.stderr); raise SystemExit(2)
