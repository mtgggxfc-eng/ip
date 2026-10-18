import base64
import datetime
import importlib.util
import ipaddress
import json
import pathlib
import socket
import ssl
import tempfile
import threading
import time
import urllib.request
import urllib.error

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

root = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('panel_tls', root/'residential-panel.py')
panel = importlib.util.module_from_spec(spec); spec.loader.exec_module(panel)
servers = []
original_server = panel.ThreadingHTTPServer
def tracked_server(*args, **kwargs):
    server = original_server(*args, **kwargs); servers.append(server); return server
panel.ThreadingHTTPServer = tracked_server
def port():
    with socket.socket() as test:
        test.bind(('127.0.0.1', 0)); return test.getsockname()[1]
with tempfile.TemporaryDirectory() as directory:
    directory = pathlib.Path(directory)
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'127.0.0.1')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    cert_path=directory/'public.pem'; key_path=directory/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    panel.ROOT=directory; panel.SETTINGS=directory/'settings.json'; panel.AUTH=directory/'auth.json'
    panel.STATUS=directory/'status.json'
    public_port, internal_port=port(),port()
    panel.write_json(panel.SETTINGS,{'panel_port':public_port,'agent_port':internal_port,'public_cert':str(cert_path),'public_key':str(key_path),
        'country':'US','proxy_port':42000,'mode':'auto','manual_node_ip':'','switch_revision':0})
    panel.write_json(panel.AUTH,{'username':'user','password':'password'})
    threading.Thread(target=panel.main,daemon=True).start()
    context=ssl.create_default_context(cafile=str(cert_path))
    headers={'Authorization':'Basic '+base64.b64encode(b'user:password').decode()}
    try:
        for scheme,p in [('https',public_port),('http',internal_port)]:
            for attempt in range(30):
                try:
                    with urllib.request.urlopen(urllib.request.Request(f'{scheme}://127.0.0.1:{p}/api/config',headers=headers),context=context,timeout=1) as response:
                        assert json.load(response)['slot_map']['0']=='US'
                    break
                except urllib.error.URLError:
                    time.sleep(0.05)
            else: raise AssertionError('listener did not start')
    finally:
        for server in servers: server.shutdown(); server.server_close()
print('public TLS certificate selection and loopback HTTP agent API: OK')
