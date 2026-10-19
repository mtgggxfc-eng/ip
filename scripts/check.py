#!/usr/bin/env python3
"""Local checks only; no SSH, cloud modifications or real firewall operations."""
import ast
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(args): subprocess.run(args, cwd=ROOT, check=True)


def main():
    for path in ROOT.rglob('*.py'):
        if not any(p in path.parts for p in ('.venv', '__pycache__')): ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    generated = ['jkyj.sh', 'gengxin.sh', 'xiezai.sh', 'hub/panel.html', 'residential-panel.py']
    before = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in generated}
    run([sys.executable, 'scripts/build.py'])
    assert before == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in generated}, '请先运行构建并保存生成文件'
    git_bash = pathlib.Path('C:/Program Files/Git/bin/bash.exe')
    bash = str(git_bash) if os.name == 'nt' and git_bash.exists() else shutil.which('bash')
    if not bash: raise RuntimeError('检查需要 Bash')
    for path in ('jkyj.sh', 'gengxin.sh', 'xiezai.sh', 'bootstrap/install.sh'): run([bash, '-n', path])
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_network.py', '-v'])
    for path in ('tests/test_panel.py', 'tests/test_panel_tls.py', 'tests/test_uninstaller.py'): run([sys.executable, path])
    node = shutil.which('node')
    if not node: raise RuntimeError('汇总权限检查需要 Node 20 或更高版本')
    run([node, 'tests/test_hub.mjs'])
    run([node, 'tests/test_bootstrap.mjs'])
    print('本地检查通过；不代表真实 VPS、Cloudflare 或客户端公网验收。')


if __name__ == '__main__': main()
