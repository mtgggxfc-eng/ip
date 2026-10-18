#!/usr/bin/env python3
"""Test a share URI using an isolated sing-box process; never edit client settings."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
import pathlib
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlsplit


def outbound_for(link):
    u = urlsplit(link)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if u.scheme not in ('vless', 'trojan', 'hysteria2', 'hy2') or not u.hostname or not u.port:
        raise ValueError('不支持的节点链接或端口缺失')
    result = {'type': 'hysteria2' if u.scheme == 'hy2' else u.scheme,
              'tag': 'proxy', 'server': u.hostname, 'server_port': u.port}
    if result['type'] == 'vless':
        result['uuid'] = unquote(u.username or '')
        if q.get('flow'): result['flow'] = q['flow']
    else:
        result['password'] = unquote(u.username or '')
    security = q.get('security', 'tls' if result['type'] != 'vless' else 'none')
    if security in ('tls', 'reality'):
        tls = {'enabled': True, 'server_name': q.get('sni', u.hostname)}
        if security == 'reality':
            tls.update(utls={'enabled': True, 'fingerprint': q.get('fp', 'chrome')},
                       reality={'enabled': True, 'public_key': q['pbk'], 'short_id': q.get('sid', '')})
        elif result['type'] == 'hysteria2' and q.get('insecure') == '1':
            # URI pinSHA256 is a whole-certificate digest, not a public-key digest.
            # sing-box does not expose that exact URI pin; require the actual cert
            # during verification rather than silently disabling authentication.
            cert = os.environ.get('IP_HY2_CERT')
            if not cert or not q.get('pinSHA256'):
                raise ValueError('自签名 HY2 验证需要 IP_HY2_CERT 和 pinSHA256；不会跳过证书认证')
            pem = pathlib.Path(cert).read_text()
            actual = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            if actual.lower() != q['pinSHA256'].replace(':', '').lower():
                raise ValueError('HY2 证书与链接指纹不符')
            tls['certificate'] = pem
        result['tls'] = tls
    if q.get('type') == 'ws':
        result['transport'] = {'type': 'ws', 'path': q.get('path', '/'),
                               'headers': {'Host': q.get('host', u.hostname)}}
    if q.get('obfs') == 'salamander':
        result['obfs'] = {'type': 'salamander', 'password': q.get('obfs-password', '')}
    return result


def verify(link, binary, repeats=2, where='public-self-test'):
    started = time.monotonic()
    report = {'label': unquote(urlsplit(link).fragment), 'scope': where,
              'tested_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'ok': False, 'attempts': []}
    try:
        outbound = outbound_for(link)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        config = {'log': {'level': 'error'}, 'inbounds': [
            {'type': 'socks', 'listen': '127.0.0.1', 'listen_port': port}],
            'outbounds': [outbound], 'route': {'final': 'proxy'}}
        with tempfile.TemporaryDirectory(prefix='ip-verify-') as directory:
            p = pathlib.Path(directory) / 'client.json'
            p.write_text(json.dumps(config), encoding='utf-8'); p.chmod(0o600)
            checked = subprocess.run([binary, 'check', '-c', str(p)], capture_output=True, timeout=15)
            if checked.returncode: raise ValueError('测试客户端配置检查失败')
            with (pathlib.Path(directory) / 'client.log').open('wb') as log:
                client = subprocess.Popen([binary, 'run', '-c', str(p)], stdout=log, stderr=log)
                try:
                    time.sleep(1)
                    for _ in range(repeats):
                        begin = time.monotonic()
                        request = subprocess.run(['curl.exe' if os.name == 'nt' else 'curl',
                            '-4fsS', '--noproxy', '', '--max-time', '15', '--socks5-hostname', '127.0.0.1:' + str(port),
                            'https://api.ipify.org'], capture_output=True, text=True, timeout=18)
                        ip = request.stdout.strip()
                        ok = request.returncode == 0
                        try: ipaddress.IPv4Address(ip)
                        except ValueError: ok = False
                        report['attempts'].append({'ok': ok, 'exit_ip': ip if ok else '',
                            'elapsed_ms': round((time.monotonic() - begin) * 1000)})
                        if not ok: break
                    report['ok'] = len(report['attempts']) == repeats and all(a['ok'] for a in report['attempts'])
                    if not report['ok']: report['reason'] = '公开入口握手或 HTTPS 出站失败'
                finally:
                    client.terminate()
                    try: client.wait(timeout=5)
                    except subprocess.TimeoutExpired: client.kill(); client.wait(timeout=5)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # Do not include the URI or subprocess argv in diagnostics.
        report['reason'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    report['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--links', required=True, type=pathlib.Path)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--scope', choices=['client-test', 'public-self-test'], default='client-test')
    args = parser.parse_args()
    reports = [verify(l.strip(), args.binary, where=args.scope) for l in args.links.read_text(encoding='utf-8').splitlines() if l.strip()]
    value = json.dumps(reports, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(value + '\n', encoding='utf-8'); args.report.chmod(0o600)
    print(value)
    return 0 if reports and all(r['ok'] for r in reports) else 2


if __name__ == '__main__':
    raise SystemExit(main())
