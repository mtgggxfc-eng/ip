#!/usr/bin/env python3
"""Read active sing-box services and recover import links without changing the host."""
import ast
import base64
import hashlib
import json
import os
import pathlib
import re
import socket
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request


def x25519_public(private_b64):
    raw = bytearray(base64.urlsafe_b64decode(private_b64 + '=' * (-len(private_b64) % 4)))
    if len(raw) != 32:
        raise ValueError('Reality private key length')
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    scalar = int.from_bytes(raw, 'little')
    prime = 2**255 - 19
    x1, x2, z2, x3, z3, swap = 9, 1, 0, 9, 1, 0
    for t in range(254, -1, -1):
        bit = (scalar >> t) & 1
        swap ^= bit
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = bit
        a = (x2 + z2) % prime
        aa = a*a % prime
        b = (x2 - z2) % prime
        bb = b*b % prime
        e = (aa - bb) % prime
        c = (x3 + z3) % prime
        d = (x3 - z3) % prime
        da = d*a % prime
        cb = c*b % prime
        x3 = (da + cb)**2 % prime
        z3 = x1 * (da - cb)**2 % prime
        x2 = aa*bb % prime
        z2 = e * (aa + 121665*e) % prime
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    public = (x2 * pow(z2, prime-2, prime) % prime).to_bytes(32, 'little')
    return base64.urlsafe_b64encode(public).decode().rstrip('=')


def run(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=8).stdout.strip()


def public_ip():
    for url in ('https://api.ipify.org', 'https://ifconfig.me/ip'):
        try:
            with urllib.request.urlopen(url, timeout=7) as response:
                value = response.read().decode().strip()
            socket.inet_aton(value)
            return value
        except Exception:
            pass
    return ''


def active_configs():
    services = run('systemctl', 'list-units', '--type=service', '--state=active', '--plain', '--no-legend')
    found = []
    for line in services.splitlines():
        service = line.split(None, 1)[0]
        if not any(word in service.lower() for word in ('sing-box', 'sing_box', 's-box')):
            continue
        command = run('systemctl', 'show', '-p', 'ExecStart', '--value', service)
        matches = re.findall(r'(?:^|\s)-(?:c|-config)(?:=|\s+)([^\s;]+)', command)
        for name in matches:
            path = pathlib.Path(name)
            if path.is_file() and path not in found:
                found.append(path)
    return found


def name_from_cert(path):
    try:
        info = ssl._ssl._test_decode_cert(str(path))
        names = [v for typ, v in info.get('subjectAltName', ()) if typ == 'DNS']
        return names[0] if names else ''
    except Exception:
        return ''


def link_for_reality(inbound, ip, label):
    tls = inbound.get('tls') or {}
    reality = tls.get('reality') or {}
    users = inbound.get('users') or []
    if inbound.get('type') != 'vless' or not reality.get('enabled') or not users:
        return ''
    user = users[0]
    uuid = user.get('uuid')
    private = reality.get('private_key')
    if not uuid or not private:
        return ''
    try:
        public = x25519_public(private)
    except Exception:
        return ''
    short_ids = reality.get('short_id') or ['']
    sni = tls.get('server_name') or (reality.get('handshake') or {}).get('server') or 'www.amazon.com'
    params = {'encryption':'none','security':'reality','sni':sni,'fp':'chrome','pbk':public,
              'sid':short_ids[0],'type':'tcp'}
    if user.get('flow'):
        params['flow'] = user['flow']
    return f"vless://{uuid}@{ip}:{inbound['listen_port']}?{urllib.parse.urlencode(params)}#{urllib.parse.quote(label)}"


def link_for_hy2(inbound, ip, label):
    if inbound.get('type') != 'hysteria2' or not inbound.get('users'):
        return ''
    tls = inbound.get('tls') or {}
    password = inbound['users'][0].get('password')
    if not password:
        return ''
    cert = pathlib.Path(tls.get('certificate_path') or '')
    sni = tls.get('server_name') or name_from_cert(cert)
    params = {'insecure':'1'}
    if sni:
        params['sni'] = sni
    try:
        der = ssl.PEM_cert_to_DER_cert(cert.read_text())
        params['pinSHA256'] = hashlib.sha256(der).hexdigest().upper()
    except Exception:
        pass
    if (inbound.get('obfs') or {}).get('type') == 'salamander':
        params['obfs'] = 'salamander'
        params['obfs-password'] = inbound['obfs'].get('password', '')
    return f"hysteria2://{urllib.parse.quote(password, safe='')}@{ip}:{inbound['listen_port']}/?{urllib.parse.urlencode(params)}#{urllib.parse.quote(label)}"


def is_home(inbound, config):
    tag = inbound.get('tag')
    outbounds = {item.get('tag'): item for item in config.get('outbounds', [])}
    route = config.get('route') or {}
    destinations = []
    for rule in route.get('rules', []):
        if tag in (rule.get('inbound') or []):
            destinations.append(rule.get('outbound'))
        if rule.get('auth_user'):
            users = {u.get('name') for u in inbound.get('users', [])}
            if users.intersection(rule['auth_user']):
                destinations.append(rule.get('outbound'))
    destinations.append(route.get('final'))
    return any(outbounds.get(dest, {}).get('type') == 'socks' and
               outbounds[dest].get('server') in ('127.0.0.1','localhost','::1')
               for dest in destinations if dest in outbounds)


def discover():
    ip = public_ip()
    result = {'ip': ip, 'tcp': '', 'udp': '', 'home': '', 'managed_tcp': '', 'managed_udp': '', 'managed_home': '', 'panel_url': '',
              'panel_username': '', 'panel_password': '', 'source_configs': []}
    if not ip:
        return result
    for path in active_configs():
        try:
            config = json.loads(path.read_text())
        except Exception:
            continue
        result['source_configs'].append(str(path))
        managed = str(path) == '/etc/sing-box/three-nodes.json'
        for inbound in config.get('inbounds', []):
            if inbound.get('type') == 'vless' and (inbound.get('tls') or {}).get('reality', {}).get('enabled'):
                if is_home(inbound, config):
                    link = link_for_reality(inbound, ip, 'Residential-Exit')
                    result['home'] = result['home'] or link
                    if managed: result['managed_home'] = link
                else:
                    link = link_for_reality(inbound, ip, 'VLESS-Reality-TCP')
                    result['tcp'] = result['tcp'] or link
                    if managed: result['managed_tcp'] = link
            elif inbound.get('type') == 'hysteria2':
                link = link_for_hy2(inbound, ip, 'Hysteria2-UDP')
                result['udp'] = result['udp'] or link
                if managed: result['managed_udp'] = link
    if result['managed_home']:
        result['home'] = result['managed_home']
    settings = pathlib.Path('/etc/residential-stack/settings.json')
    auth = pathlib.Path('/etc/residential-stack/auth.json')
    if settings.is_file() and auth.is_file():
        try:
            values = json.loads(settings.read_text())
            creds = json.loads(auth.read_text())
            result.update(panel_url=f"https://{ip}:{values['panel_port']}/",
                          panel_username=creds['username'], panel_password=creds['password'])
        except Exception:
            pass
    elif result['home']:
        legacy = pathlib.Path('/opt/proxy_lite/lite_manager.py')
        if legacy.is_file():
            try:
                values = {}
                for node in ast.parse(legacy.read_text()).body:
                    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                        if node.targets[0].id in ('C2_URL', 'WEB_USER', 'WEB_PASS'):
                            values[node.targets[0].id] = ast.literal_eval(node.value)
                if str(values.get('C2_URL', '')).startswith('https://'):
                    result.update(panel_url=values.get('C2_URL', ''),
                                  panel_username=values.get('WEB_USER', ''),
                                  panel_password=values.get('WEB_PASS', ''))
            except Exception:
                pass
    return result


if __name__ == '__main__':
    output = discover()
    if '--summary' in sys.argv:
        print(json.dumps({'found':{key: bool(output[key]) for key in ('tcp','udp','home','panel_url')},
                          'source_configs': output['source_configs']}))
    else:
        print(json.dumps(output, ensure_ascii=False))
