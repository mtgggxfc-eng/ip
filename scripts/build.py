#!/usr/bin/env python3
"""Rebuild self-contained public entrypoints from reviewable source files."""
import base64
import gzip
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def encoded(name):
    return base64.b64encode((ROOT / name).read_bytes()).decode('ascii')


def build():
    (ROOT / 'hub/panel.html').write_bytes((ROOT / 'panel-original.html').read_bytes())
    panel = ROOT / 'residential-panel.py'
    source = panel.read_text(encoding='utf-8')
    source, count = re.subn(r"PAGE = base64\.b64decode\('[A-Za-z0-9+/=]+'\)",
        lambda _: "PAGE = base64.b64decode('" + encoded('panel-original.html') + "')", source, count=1)
    assert count == 1, 'panel HTML marker'
    panel.write_text(source, encoding='utf-8', newline='\n')
    replacements = {'__PANEL_B64__': encoded('residential-panel.py'),
        '__AGENT_B64__': encoded('residential-agent.py'), '__PROXY_B64__': encoded('residential-proxy.py'),
        '__DISCOVERY_B64__': encoded('discover-existing.py'), '__NETWORK_B64__': encoded('network-adapter.py'),
        '__VERIFY_B64__': encoded('verify-node.py')}
    updater = (ROOT / 'src/update.sh').read_text(encoding='utf-8')
    for marker, value in replacements.items(): updater = updater.replace(marker, value)
    (ROOT / 'gengxin.sh').write_text(updater, encoding='utf-8', newline='\n')
    replacements['__UPDATER_B64__'] = encoded('gengxin.sh')
    installer = (ROOT / 'src/install.sh').read_text(encoding='utf-8')
    for marker, value in replacements.items(): installer = installer.replace(marker, value)
    assert not re.search(r'__[A-Z_]+_B64__', installer), 'unresolved source marker'
    (ROOT / 'jkyj.sh').write_text(installer, encoding='utf-8', newline='\n')
    # Local compatibility name; the public entrypoint is jkyj.sh.
    (ROOT / 'install-three-nodes-vps.sh').write_text(installer, encoding='utf-8', newline='\n')
    uninstall = ('#!/usr/bin/env bash\nset -Eeuo pipefail\n'
        'command -v python3 >/dev/null || { printf "未找到 Python，卸载未执行。\\n" >&2; exit 1; }\n'
        "python3 - \"$@\" <<'PY'\n" + (ROOT / 'xiezai.py').read_text(encoding='utf-8') + '\nPY\n')
    (ROOT / 'xiezai.sh').write_text(uninstall, encoding='utf-8', newline='\n')
    fallback = "printf '%s' '" + base64.b64encode(gzip.compress(installer.encode(), mtime=0)).decode() + "' | base64 -d | gzip -d | { if [ \"$(id -u)\" -eq 0 ]; then bash; else sudo bash; fi; }\n"
    (ROOT / '新VPS一条命令.txt').write_text(fallback, encoding='utf-8', newline='\n')
    for name in ('jkyj.sh', 'gengxin.sh', 'xiezai.sh'):
        print(name, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())


if __name__ == '__main__': build()
