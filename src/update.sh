#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
STACK_DIR=/etc/residential-stack
[[ $EUID -eq 0 ]] || { printf '面板更新需要 root 权限。\n' >&2; exit 1; }
[[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]] || { printf '没有找到本脚本管理的住宅面板。\n' >&2; exit 1; }
printf '\n==> 更新为原版住宅面板布局，保留节点和登录信息\n'
printf '%s' '__PANEL_B64__' | base64 -d > "$STACK_DIR/panel.py.new"
printf '%s' '__AGENT_B64__' | base64 -d > /opt/residential-stack-agent/lite_manager.py.new
python3 -m py_compile "$STACK_DIR/panel.py.new" /opt/residential-stack-agent/lite_manager.py.new
PANEL_CHANGED=0
AGENT_CHANGED=0
cmp -s "$STACK_DIR/panel.py.new" "$STACK_DIR/panel.py" || PANEL_CHANGED=1
cmp -s /opt/residential-stack-agent/lite_manager.py.new /opt/residential-stack-agent/lite_manager.py || AGENT_CHANGED=1
INTERNAL_CHANGED="$(python3 -c 'import json; s=json.load(open("/etc/residential-stack/settings.json")); print(0 if s.get("agent_port") else 1)')"
IP_PUBLIC="${IP_PUBLIC:-$(curl -4fsS --max-time 12 https://api.ipify.org)}"
export IP_PUBLIC
python3 - <<'PY'
import ipaddress,json,os,pathlib,socket
p=pathlib.Path('/etc/residential-stack/settings.json')
s=json.loads(p.read_text()); s['public_ip']=str(ipaddress.IPv4Address(os.environ['IP_PUBLIC']))
if not s.get('agent_port'):
    with socket.socket() as test:
        test.bind(('127.0.0.1',0)); s['agent_port']=test.getsockname()[1]
t=p.with_name(p.name+'.tmp'); t.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); t.chmod(0o600); os.replace(t,p)
links={k:os.environ.get(v,'') for k,v in [('tcp','IP_LINK_TCP'),('udp','IP_LINK_UDP'),('home','IP_LINK_HOME')]}
if not any(v in os.environ for v in ("IP_LINK_TCP","IP_LINK_UDP","IP_LINK_HOME")):
    import re
    saved=pathlib.Path('/root/three-nodes-info.txt')
    found=re.findall(r'(?:vless|hysteria2)://[^\s]+', saved.read_text() if saved.exists() else '')
    if len(found)>=3: links=dict(zip(('tcp','udp','home'),found[:3]))
if any(links.values()):
    native=pathlib.Path('/etc/residential-stack/native-links.json'); native.write_text(json.dumps(links,indent=2)+'\n'); native.chmod(0o600)
    p=pathlib.Path('/etc/residential-stack/links.json'); p.write_text(json.dumps(links,indent=2)+'\n'); p.chmod(0o600)
PY
mv -f "$STACK_DIR/panel.py.new" "$STACK_DIR/panel.py"
mv -f /opt/residential-stack-agent/lite_manager.py.new /opt/residential-stack-agent/lite_manager.py
if [[ "$PANEL_CHANGED" == 1 || "$INTERNAL_CHANGED" == 1 ]] || ! systemctl is-active --quiet residential-panel.service; then
  systemctl restart residential-panel.service
fi
if [[ "$AGENT_CHANGED" == 1 || "$INTERNAL_CHANGED" == 1 ]] || ! systemctl is-active --quiet residential-agent.service; then
  systemctl restart residential-agent.service
else
  printf '住宅代理代码与内部接口未变化，保留当前连接。\n'
fi

if [[ "${IP_SKIP_CERT:-0}" == 1 ]]; then
  printf '本机使用隧道或域名证书，不重复申请公网 IP 证书。\n'
  exit 0
fi
cat > "$STACK_DIR/apply-panel-cert.sh" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path('/etc/residential-stack/settings.json'); s=json.loads(p.read_text())
base='/etc/residential-stack/acme/live/residential-panel-ip/'
s.update(public_cert=base+'fullchain.pem',public_key=base+'privkey.pem')
t=p.with_name(p.name+'.tmp'); t.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); t.chmod(0o600); os.replace(t,p)
PY
systemctl restart residential-panel.service
HOOK
chmod 700 "$STACK_DIR/apply-panel-cert.sh"
cat > "$STACK_DIR/renew-panel-cert.sh" <<'RENEW'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/residential-stack
CB=/opt/residential-certbot/bin/certbot
IP="$(python3 -c 'import json; print(json.load(open("/etc/residential-stack/settings.json"))["public_ip"])')"
COMMON=(--config-dir "$ROOT/acme" --work-dir "$ROOT/acme-work" --logs-dir "$ROOT/acme-logs")
# Reuse the scoped firewall adapter for initial issuance and timer renewals.
# Cloud security groups still need the owner's authorization.
python3 - <<'PY'
import pathlib,runpy
p=pathlib.Path('/etc/residential-stack/network-adapter.py')
if p.is_file():
    try:
        result=runpy.run_path(str(p))['firewall']([('tcp',80)])
        if not all(item['host_opened'] for item in result): print('证书验证的本机 TCP 80 放行失败，请检查系统防火墙。')
    except (OSError,RuntimeError,ValueError):
        print('证书验证端口未能自动放行，请检查系统防火墙。')
PY
if [[ -f "$ROOT/acme/renewal/residential-panel-ip.conf" ]]; then
  "$CB" renew --cert-name residential-panel-ip --non-interactive --no-random-sleep-on-renew "${COMMON[@]}" --deploy-hook "$ROOT/apply-panel-cert.sh"
else
  "$CB" certonly --standalone --preferred-profile shortlived --ip-address "$IP" \
    --cert-name residential-panel-ip --non-interactive --agree-tos --register-unsafely-without-email \
    --keep-until-expiring "${COMMON[@]}" --deploy-hook "$ROOT/apply-panel-cert.sh"
fi
RENEW
chmod 700 "$STACK_DIR/renew-panel-cert.sh"

if [[ "${IP_SKIP_CERT:-0}" != 1 ]]; then
  printf '\n==> 自动申请 Let’s Encrypt 公网 IP 证书（需要公网 TCP 80）\n'
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
    printf '正式证书组件 Certbot 5.4 需要 Python 3.10 或以上；当前系统 Python 版本过旧。原版面板已更新，当前继续使用临时证书。\n' >&2
    exit 0
  fi
  if [[ ! -x /opt/residential-certbot/bin/certbot ]]; then
    if ! python3 -m venv /opt/residential-certbot; then
      apt-get update -q
      DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
      python3 -m venv /opt/residential-certbot
    fi
    if ! /opt/residential-certbot/bin/pip --disable-pip-version-check install 'certbot==5.4.0'; then
      printf '证书组件安装失败，当前面板和节点继续运行。检查 PyPI 网络后重新执行命令重试。\n' >&2
      exit 0
    fi
  fi
  if command -v ufw >/dev/null 2>&1 && LC_ALL=C ufw status 2>/dev/null | grep -q 'Status: active'; then
    ufw allow 80/tcp >/dev/null
  fi
  cat > /etc/systemd/system/residential-cert-renew.service <<'UNIT'
[Unit]
Description=Renew trusted residential panel IP certificate
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/etc/residential-stack/renew-panel-cert.sh
TimeoutStartSec=240
UNIT
  cat > /etc/systemd/system/residential-cert-renew.timer <<'UNIT'
[Unit]
Description=Check residential panel IP certificate twice daily
[Timer]
OnBootSec=10min
OnUnitActiveSec=12h
RandomizedDelaySec=30min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now residential-cert-renew.timer >/dev/null
  if ! "$STACK_DIR/renew-panel-cert.sh"; then
    printf '\n正式证书申请/续期失败。请检查云平台是否放行公网 TCP 80，以及 80 端口是否被其他服务占用。\n' >&2
    printf '详细原因：/etc/residential-stack/acme-logs/letsencrypt.log；定时任务会继续重试，也可再次执行本命令。\n' >&2
  fi
  if [[ -f "$STACK_DIR/acme/live/residential-panel-ip/fullchain.pem" ]] && openssl x509 -in "$STACK_DIR/acme/live/residential-panel-ip/fullchain.pem" -checkend 0 -noout >/dev/null; then
    "$STACK_DIR/apply-panel-cert.sh"
    printf '面板已加载正式 IP 证书，自动续期已开启。请关闭旧页面并重新打开面板地址。\n'
  else
    printf '当前仍使用临时证书，浏览器会提示不受信任；三条节点不受影响。\n' >&2
  fi
fi
