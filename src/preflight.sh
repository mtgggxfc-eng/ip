#!/usr/bin/env bash
# Scoped environment repairs only. No kernel tuning, DNS replacement or reboot.
set -Eeuo pipefail
umask 077
if ! systemctl show --property=Version --value >/dev/null 2>&1; then
  printf '当前环境无法使用 systemd 管理服务；请使用支持 systemd 的 Debian/Ubuntu 主机。\n' >&2
  exit 2
fi
required=()
for pair in curl:curl openssl:openssl ip:iproute2 ss:iproute2 iptables:iptables openvpn:openvpn cron:cron flock:util-linux; do
  tool="${pair%%:*}"; package="${pair#*:}"
  command -v "$tool" >/dev/null || required+=("$package")
done
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || required+=(ca-certificates)
if [[ ${#required[@]} -gt 0 ]]; then
  printf '正在补全缺失的软件组件：%s\n' "${required[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y "${required[@]}"
else
  printf '基础组件已齐全，保留当前版本。\n'
fi
if [[ ! -c /dev/net/tun ]]; then
  if command -v modprobe >/dev/null; then modprobe tun 2>/dev/null || true; fi
  if [[ ! -e /dev/net/tun && ! -L /dev/net/tun && ! -L /dev/net ]]; then
    install -d /dev/net
    mknod /dev/net/tun c 10 200 2>/dev/null || true
  fi
fi
python3 - "$1" <<'PY'
import datetime,json,os,pathlib,platform,shutil,socket,stat,subprocess,sys
report={'schema':1,'project':'ip','python':platform.python_version(),'architecture':platform.machine(),'disk_free_bytes':shutil.disk_usage('/opt').free,'tun':False,'warnings':[]}
try:
    with open('/dev/net/tun','rb',buffering=0): pass
    report['tun']=stat.S_ISCHR(os.stat('/dev/net/tun').st_mode)
except OSError:
    report['warnings'].append('TUN 设备不可用；虚拟化平台可能需要后台启用，无法由脚本强制解除。')
try:
    cap=next(line.split()[1] for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
    report['net_admin']=bool(int(cap,16)&(1<<12))
    if not report['net_admin']:report['warnings'].append('当前环境缺少 NET_ADMIN 权限，住宅隧道可能需要平台后台授权。')
except (OSError,StopIteration,ValueError):report['net_admin']=None
report['dns']={}
socket.setdefaulttimeout(5)
for host in ('github.com','pypi.org','acme-v02.api.letsencrypt.org'):
    try: socket.getaddrinfo(host,443);report['dns'][host]=True
    except OSError:report['dns'][host]=False;report['warnings'].append('DNS 无法解析 '+host+'，请检查平台网络；未修改系统 DNS。')
try:
    report['ntp_synchronized']=subprocess.run(['timedatectl','show','-p','NTPSynchronized','--value'],capture_output=True,text=True,timeout=5).stdout.strip()=='yes'
except (OSError,subprocess.SubprocessError):report['ntp_synchronized']=None
if report['disk_free_bytes'] < 500*1024*1024:report['warnings'].append('可用空间不足 500 MiB，可能无法补全证书运行环境。')
if datetime.datetime.utcnow().year < 2025:report['warnings'].append('系统时间异常，可能影响 HTTPS 证书；请核对平台时钟。')
path=pathlib.Path(sys.argv[1]);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
for message in report['warnings']:print('环境提醒：'+message)
if sys.version_info<(3,10):print('系统 Python 较旧；证书组件将自动使用独立 Python，保留系统版本。')
print('环境检查完成。')
PY
