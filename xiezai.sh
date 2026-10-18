#!/usr/bin/env bash
set -Eeuo pipefail
if ! command -v python3 >/dev/null 2>&1; then
  printf '未找到 python3，无法核对组件归属；卸载未执行。\n' >&2
  exit 1
fi
python3 - "$@" <<'PY'
#!/usr/bin/env python3
"""Remove only components owned by the ip installer; never remove shared packages."""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


UNIT_SIGNATURES = {
    'residential-panel.service': ('Description=Independent Residential Exit Management Panel',
        'ExecStart=/usr/bin/python3 -u /etc/residential-stack/panel.py'),
    'residential-agent.service': ('Description=Residential Proxy Core Engine',
        'ExecStart=/usr/bin/python3 -u /opt/residential-stack-agent/lite_manager.py'),
    'three-nodes.service': ('Description=Three proxy entries (VLESS Reality, Hysteria2, residential)',
        r'^ExecStart=\S+ run -c /etc/sing-box/three-nodes\.json$'),
    'residential-cert-renew.service': ('Description=Renew trusted residential panel IP certificate',
        'ExecStart=/etc/residential-stack/renew-panel-cert.sh'),
    'residential-cert-renew.timer': ('Description=Check residential panel IP certificate twice daily',
        '[Timer]'),
}


@dataclass
class Plan:
    base: pathlib.Path = pathlib.Path('/')
    units: list = field(default_factory=list)
    files: list = field(default_factory=list)
    directories: list = field(default_factory=list)
    preserved: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)

    def path(self, absolute):
        return self.base / absolute.lstrip('/')


def text(path):
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return ''


def exists(path):
    return path.exists() or path.is_symlink()


def add_target(plan, absolute, directory=False):
    path = plan.path(absolute)
    if not exists(path):
        return
    if path.is_symlink() or path.resolve() != path.absolute():
        plan.conflicts.append(f'路径经过符号链接，未删除：{absolute}')
        return
    (plan.directories if directory else plan.files).append(path)


def inventory(base=pathlib.Path('/')):
    plan = Plan(base=base.absolute())
    for name, signatures in UNIT_SIGNATURES.items():
        path = plan.path('/etc/systemd/system/' + name)
        if not exists(path):
            continue
        source = text(path)
        match = all(re.search(signature, source, re.M) if signature.startswith('^') else signature in source.splitlines()
                    for signature in signatures)
        if path.is_symlink() or not match:
            plan.conflicts.append(f'同名服务归属无法确认：{name}')
            continue
        plan.units.append(name)
        add_target(plan, '/etc/systemd/system/' + name)
        drop_in = plan.path('/etc/systemd/system/' + name + '.d')
        if exists(drop_in):
            plan.preserved.append('自定义服务附加配置：' + str(drop_in))
    stack = plan.path('/etc/residential-stack')
    panel_source = text(stack/'panel.py')
    stack_owned = ('residential-panel.service' in plan.units or
                   'residential-cert-renew.service' in plan.units or
                   ('ResidentialPanel/1.0' in panel_source and (stack/'settings.json').is_file() and (stack/'auth.json').is_file()))
    agent = plan.path('/opt/residential-stack-agent')
    agent_source = text(agent/'lite_manager.py')
    agent_owned = ('residential-agent.service' in plan.units or
                   ('WORKSPACE = Path("/opt/residential-stack-agent")' in agent_source and 'C2_URL' in agent_source))
    config = plan.path('/etc/sing-box/three-nodes.json')
    node_owned = 'three-nodes.service' in plan.units
    if config.is_file() and (node_owned or stack_owned):
        try:
            value = json.loads(config.read_text(encoding='utf-8'))
            tags = {inbound.get('tag') for inbound in value.get('inbounds', [])}
            if not tags or not tags <= {'reality-direct', 'hy2-direct', 'reality-home'}:
                raise ValueError('存在不属于本安装器的入口')
            node_owned = True
            add_target(plan, '/etc/sing-box/three-nodes.json')
            add_target(plan, '/etc/sing-box/three-nodes.json.new')
        except (ValueError, OSError, TypeError) as error:
            plan.conflicts.append('节点配置归属无法确认：/etc/sing-box/three-nodes.json；' + str(error))
    if stack_owned:
        add_target(plan, '/etc/residential-stack', directory=True)
    elif exists(stack):
        plan.preserved.append('未识别的目录：/etc/residential-stack')
    if agent_owned:
        add_target(plan, '/opt/residential-stack-agent', directory=True)
    elif exists(agent):
        plan.preserved.append('未识别的目录：/opt/residential-stack-agent')
    if node_owned or stack_owned:
        binary_dir = plan.path('/opt/residential-stack-bin')
        if exists(binary_dir):
            if binary_dir.is_dir() and not binary_dir.is_symlink() and set(p.name for p in binary_dir.iterdir()) <= {'sing-box'}:
                add_target(plan, '/opt/residential-stack-bin', directory=True)
            else:
                plan.preserved.append('包含额外文件的目录：/opt/residential-stack-bin')
    certbot = plan.path('/opt/residential-certbot')
    if stack_owned and exists(certbot):
        if certbot.is_dir() and not certbot.is_symlink() and (certbot/'pyvenv.cfg').is_file():
            add_target(plan, '/opt/residential-certbot', directory=True)
        else:
            plan.preserved.append('无法确认的证书程序目录：/opt/residential-certbot')
    links = plan.path('/root/three-nodes-links.txt')
    if re.fullmatch(r'(?:vless|hysteria2)://[^\s]+\n(?:vless|hysteria2)://[^\s]+\n(?:vless|hysteria2)://[^\s]+\n?', text(links)):
        add_target(plan, '/root/three-nodes-links.txt')
    info = plan.path('/root/three-nodes-info.txt')
    saved = text(info)
    if ('TCP 节点状态：' in saved and '住宅管理面板：' in saved) or (stack_owned and 'vless://' in saved and '面板' in saved):
        add_target(plan, '/root/three-nodes-info.txt')
    if node_owned:
        for name in ('hy2-cert.pem', 'hy2-key.pem'):
            if exists(plan.path('/etc/sing-box/' + name)):
                plan.preserved.append('可能被其他服务复用的证书：/etc/sing-box/' + name)
    return plan


def run_command(args, check=False):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def execute(plan, runner=run_command):
    if plan.conflicts:
        raise RuntimeError('\n'.join(plan.conflicts))
    owned_rules = []
    if 'residential-agent.service' in plan.units:
        result = runner(['ip', '-j', 'rule', 'show'])
        if result.returncode == 0:
            try:
                owned_rules = [r for r in json.loads(result.stdout) if str(r.get('table')) == '190'
                               and (r.get('oif') or r.get('oifname')) == 'tun90' and type(r.get('priority')) is int]
            except (ValueError, TypeError):
                pass
    # Stop the renewal timer before its service; stop nodes before the agent.
    order = ('residential-cert-renew.timer', 'residential-cert-renew.service', 'three-nodes.service',
             'residential-agent.service', 'residential-panel.service')
    for unit in order:
        if unit in plan.units:
            runner(['systemctl', 'stop', unit], check=True)
            result = runner(['systemctl', 'disable', unit])
            if result.returncode:
                print('提示：无法取消开机启动，将继续移除已识别的服务文件：' + unit)
    for rule in owned_rules:
        runner(['ip', 'rule', 'del', 'pref', str(rule['priority']), 'table', '190'])
    for path in plan.files + plan.directories:
        # Re-check after stopping services; never recurse through a changed symlink.
        if exists(path) and (path.is_symlink() or path.resolve() != path.absolute()):
            raise RuntimeError('卸载期间路径发生变化，已停止：' + str(path))
        if not exists(path):
            continue
        if path in plan.directories:
            shutil.rmtree(path)
        else:
            path.unlink()
    if plan.units:
        runner(['systemctl', 'daemon-reload'], check=True)
        runner(['systemctl', 'reset-failed', *plan.units])


def main():
    parser = argparse.ArgumentParser(description='卸载 ip 脚本创建的组件；保留已有节点服务和系统依赖。')
    parser.add_argument('--dry-run', action='store_true', help='只显示将卸载的内容')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('请使用 root 或 sudo 运行卸载命令。', file=sys.stderr)
        return 1
    plan = inventory()
    if plan.conflicts:
        print('卸载未执行，发现无法确认归属的内容：\n' + '\n'.join(plan.conflicts), file=sys.stderr)
        return 1
    targets = plan.files + plan.directories
    if not targets:
        print('没有找到可卸载的 ip 组件，无需处理。')
    else:
        print('将清理以下 ip 组件：')
        for path in targets:
            print('  ' + str(path))
        if args.dry_run:
            print('预览结束，未执行卸载。')
        else:
            execute(plan)
            print('卸载完成，可以重新执行安装命令进行测试。')
    print('原有 sing-box、x-ui、proxy-lite 服务及系统软件包和防火墙规则保留。')
    for note in plan.preserved:
        print('保留：' + note)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print('卸载未完成：' + str(error), file=sys.stderr)
        raise SystemExit(1)

PY
