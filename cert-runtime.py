#!/usr/bin/env python3
"""Prepare a project-local Certbot runtime, including Python 3.8 hosts."""
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = pathlib.Path('/etc/residential-stack')
VENV = pathlib.Path('/opt/residential-certbot')
UV_VERSION = '0.13.1'
UV_ASSETS = {
    'x86_64': ('x86_64', 'eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220'),
    'aarch64': ('aarch64', '9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14')}


def status(stage, ok, reason):
    path = ROOT / 'certificate-status.json'
    if ROOT.is_symlink() or path.is_symlink(): raise RuntimeError('证书状态路径为符号链接')
    fd, name = tempfile.mkstemp(prefix='certificate-status.', dir=str(ROOT))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump({'stage': stage, 'ok': ok, 'reason': reason, 'updated_at': int(time.time())}, stream, ensure_ascii=False);stream.write('\n')
        os.chmod(name,0o600);os.replace(name,str(path))
    finally:
        if os.path.exists(name):os.unlink(name)


def call(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, timeout=300, **kwargs)


def usable_python(executable):
    try:
        p = subprocess.run([str(executable), '-c', 'import sys,ssl,venv;raise SystemExit(sys.version_info < (3,10))'], capture_output=True, timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError): return False


def managed_python():
    runtime = ROOT / 'cert-runtime'
    if runtime.is_symlink(): raise RuntimeError('证书运行环境路径为符号链接')
    runtime.mkdir(mode=0o700, exist_ok=True)
    uv = runtime / 'uv'
    arch, digest = UV_ASSETS.get(platform.machine(), ('', ''))
    if not arch: raise RuntimeError('当前架构不支持自动补全证书 Python 环境')
    if uv.is_symlink(): raise RuntimeError('证书工具路径为符号链接')
    if not uv.is_file():
        name = 'uv-' + arch + '-unknown-linux-gnu'
        url = 'https://github.com/astral-sh/uv/releases/download/' + UV_VERSION + '/' + name + '.tar.gz'
        print('系统 Python 较旧，正在下载独立证书运行环境…', flush=True)
        with tempfile.TemporaryDirectory(dir=str(runtime)) as tmp:
            archive = pathlib.Path(tmp) / 'uv.tar.gz'
            request = urllib.request.Request(url, headers={'User-Agent': 'ip-installer/1.0'})
            with urllib.request.urlopen(request, timeout=90) as source, archive.open('wb') as target:
                shutil.copyfileobj(source, target)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise RuntimeError('证书运行工具 SHA256 校验失败，未执行下载文件')
            with tarfile.open(str(archive)) as tar:
                member = tar.getmember(name + '/uv')
                if not member.isfile(): raise RuntimeError('证书运行工具归档内容无效')
                with tar.extractfile(member) as source, uv.open('wb') as target: shutil.copyfileobj(source, target)
            uv.chmod(0o700)
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(runtime / 'python'), UV_CACHE_DIR=str(runtime / 'cache'), UV_PYTHON_INSTALL_BIN='0')
    call([uv, '--no-config', 'python', 'install', '--no-bin', '3.12'], env=env)
    found = call([uv, '--no-config', 'python', 'find', '--managed-python', '--no-python-downloads', '3.12'], env=env, capture_output=True, text=True).stdout.strip()
    python = pathlib.Path(found)
    if os.path.commonpath([str(python.resolve()), str(runtime.resolve())]) != str(runtime.resolve()):
        raise RuntimeError('证书 Python 路径不属于本项目')
    if not usable_python(python): raise RuntimeError('独立证书 Python 环境无法运行')
    return python


def ensure_certbot(dns=False):
    if ROOT.is_symlink() or VENV.is_symlink(): raise RuntimeError('证书目录路径为符号链接')
    cb = VENV / 'bin/certbot'
    if cb.is_file():
        try:
            version = call([cb, '--version'], capture_output=True, text=True).stdout
            numbers = re.search(r'(\d+)\.(\d+)', version)
            if numbers and tuple(map(int, numbers.groups())) >= (5,4) and not dns:
                status('runtime', True, '证书运行环境已就绪'); return str(cb)
        except (OSError, subprocess.SubprocessError): pass
    python = next((p for p in (sys.executable, shutil.which('python3.12'), shutil.which('python3.11'), shutil.which('python3.10')) if p and usable_python(p)), None)
    if not python: python = managed_python()
    if not usable_python(VENV / 'bin/python'):
        if VENV.exists() and not (VENV / 'pyvenv.cfg').exists() and any(VENV.iterdir()):
            raise RuntimeError('已有证书程序目录归属不明，未覆盖')
        try: call([python, '-m', 'venv', '--clear', VENV])
        except subprocess.CalledProcessError:
            # Distribution venv/ensurepip may be absent. Use the isolated
            # managed runtime instead of adding PPAs or changing system Python.
            python = managed_python(); call([python, '-m', 'venv', '--clear', VENV])
    call([VENV / 'bin/python', '-m', 'pip', '--disable-pip-version-check', 'install', 'certbot==5.4.0'] + (['certbot-dns-cloudflare==5.4.0'] if dns else []))
    call([cb, '--version'])
    status('runtime', True, '证书运行环境已就绪')
    return str(cb)


def issue_failure():
    log = ROOT / 'acme-logs/letsencrypt.log'
    text = log.read_text(errors='replace')[-24000:].lower() if log.exists() else ''
    reason = '证书申请失败，请查看 acme-logs/letsencrypt.log 中的具体原因。'
    for needles, message in [
        (('address already in use', 'could not bind tcp port 80'), '本机 TCP 80 已被其他服务占用，未停止其他服务。'),
        (('ratelimited', 'rate limit', 'too many certificates'), '证书服务限流，请按证书日志中的时间重试。'),
        (('timeout during connect', 'connection refused', 'fetching http://'), '证书验证服务器无法访问公网 TCP 80，请核对云平台、映射和防火墙。'),
        (('unrecognized arguments',), '证书工具参数不兼容，请查看证书日志。')]:
        if any(term in text for term in needles): reason = message; break
    status('issue', False, reason)
    return reason


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['issue-failed']: print(issue_failure())
        elif sys.argv[1:] == ['applied']: status('applied', True, '正式证书已加载，自动续期已启用。')
        else: ensure_certbot(dns='--dns' in sys.argv[1:])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        reason = '证书运行环境补全失败：' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__) + '；详情见安装日志。'
        status('runtime', False, reason); print(reason, file=sys.stderr); sys.exit(2)
