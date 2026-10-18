#!/usr/bin/env python3
"""Loopback protocol compatibility test; uses temporary configs, no v2rayN edits.

Usage: python tests/integration_ws.py --sing-box PATH [--xray PATH]
This tests the WebSocket origin and home routing, not Cloudflare reachability.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import select
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
n = load('network_integration', 'network-adapter.py')
v = load('verifier_integration', 'verify-node.py')


def receive(sock, length):
    value = b''
    while len(value) < length:
        piece = sock.recv(length - len(value))
        if not piece: raise EOFError()
        value += piece
    return value


class HTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        data = b'198.51.100.7'
        self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass


class SOCKS(socketserver.BaseRequestHandler):
    calls = 0
    def handle(self):
        s = self.request
        version, length = receive(s, 2); receive(s, length); s.sendall(b'\x05\x02')
        version, length = receive(s, 2); user = receive(s, length)
        password = receive(s, receive(s, 1)[0])
        assert user == b'home-test' and password == b'home-password'
        s.sendall(b'\x01\x00')
        version, command, zero, kind = receive(s, 4); assert command == 1
        host = socket.inet_ntoa(receive(s, 4)) if kind == 1 else receive(s, receive(s, 1)[0]).decode()
        port = struct.unpack('!H', receive(s, 2))[0]
        with socket.create_connection((host, port), timeout=5) as peer:
            SOCKS.calls += 1; s.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            while True:
                ready, _, _ = select.select([s, peer], [], [], 6)
                if not ready: return
                for source in ready:
                    data = source.recv(65536)
                    if not data: return
                    (peer if source is s else s).sendall(data)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--sing-box', required=True); parser.add_argument('--xray')
    args = parser.parse_args()
    http = ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
    socks = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SOCKS); socks.daemon_threads = True
    for service in (http, socks): threading.Thread(target=service.serve_forever, daemon=True).start()
    state = {}; config = n.ws_config(state, {'proxy_port': socks.server_address[1]},
                                      {'socks_username': 'home-test', 'socks_password': 'home-password'})
    with tempfile.TemporaryDirectory(prefix='ip-ws-integration-') as directory:
        folder = pathlib.Path(directory)
        config_path = folder / 'server.json'; config_path.write_text(json.dumps(config))
        subprocess.run([args.sing_box, 'check', '-c', str(config_path)], check=True)
        with (folder / 'server.log').open('wb') as log:
            server = subprocess.Popen([args.sing_box, 'run', '-c', str(config_path)], stdout=log, stderr=log)
            try:
                time.sleep(0.5)
                for engine, binary in [('sing-box', args.sing_box)] + ([('xray', args.xray)] if args.xray else []):
                    for role in ('tcp', 'udp', 'home'):
                        e = state['ws'][role]; port = n.available_port()
                        if engine == 'sing-box':
                            outbound = v.outbound_for(n.ws_uri(role, e, 'fixture.example.com'))
                            outbound.pop('tls'); outbound.update(server='127.0.0.1', server_port=e['port'])
                            client_config = {'log': {'level': 'error'}, 'inbounds': [{'type': 'socks', 'listen': '127.0.0.1', 'listen_port': port}],
                                             'outbounds': [outbound], 'route': {'final': 'proxy'}}
                            command = [binary, 'run', '-c']
                        else:
                            protocol = 'trojan' if role == 'udp' else 'vless'
                            settings = {'servers': [{'address': '127.0.0.1', 'port': e['port'], 'password': e['credential']}]} if protocol == 'trojan' else {
                                'vnext': [{'address': '127.0.0.1', 'port': e['port'], 'users': [{'id': e['credential'], 'encryption': 'none'}]}]}
                            client_config = {'log': {'loglevel': 'error'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'auth': 'noauth'}}],
                                'outbounds': [{'protocol': protocol, 'settings': settings, 'streamSettings': {'network': 'ws', 'security': 'none', 'wsSettings': {'path': e['path']}}}]}
                            command = [binary, 'run', '-config']
                        path = folder / 'client.json'; path.write_text(json.dumps(client_config))
                        with (folder / 'client.log').open('wb') as client_log:
                            client = subprocess.Popen(command + [str(path)], stdout=client_log, stderr=client_log)
                            try:
                                time.sleep(0.5); before = SOCKS.calls
                                result = subprocess.check_output(['curl.exe' if os.name == 'nt' else 'curl', '-fsS', '--noproxy', '', '--max-time', '8',
                                    '--socks5-hostname', '127.0.0.1:' + str(port), 'http://127.0.0.1:' + str(http.server_port)], text=True)
                                assert result == '198.51.100.7'
                                assert SOCKS.calls - before == (1 if role == 'home' else 0), 'wrong exit route'
                                print(engine, role, 'WebSocket handshake, authentication and exit route passed')
                            finally: client.terminate(); client.wait(timeout=5)
            finally: server.terminate(); server.wait(timeout=5)
    http.shutdown(); socks.shutdown()


if __name__ == '__main__': main()
