#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# When curl feeds this script, an early exit must drain the remaining body.
# Otherwise the producer sees a broken pipe and misleading curl error 23.
if [[ -z "${BASH_SOURCE[0]:-}" && ! -t 0 ]]; then trap 'cat >/dev/null 2>&1 || true' EXIT; fi

STACK_DIR='/etc/residential-stack'
PANEL_UNIT='/etc/systemd/system/residential-panel.service'
WORK_DIR='/opt/residential-stack-agent'
CONFIG='/etc/sing-box/three-nodes.json'
UNIT='/etc/systemd/system/three-nodes.service'
RESULT='/root/three-nodes-info.txt'
STAGE='系统预检'

error_report() {
  local code=$? line=$1
  trap - ERR
  printf '\n安装失败：%s（脚本第 %s 行，退出码 %s）\n' "$STAGE" "$line" "$code" >&2
  case "$STAGE" in
    *住宅*) printf '请检查 /dev/net/tun、OpenVPN 服务和 VPS 网络连通性。\n' >&2 ;;
    *sing-box*|*节点*) printf '请检查 sing-box 配置、系统架构和端口是否可用。\n' >&2 ;;
  esac
  if command -v systemctl >/dev/null 2>&1; then
    for service in residential-panel.service residential-agent.service three-nodes.service; do
      if systemctl list-unit-files "$service" --no-legend 2>/dev/null | grep -q "$service"; then
        if [[ -n "${IP_INSTALL_LOG:-}" ]]; then journalctl -u "$service" -n 5 --no-pager -o cat >>"$IP_INSTALL_LOG" 2>&1 || true; fi
      fi
    done
  fi
  printf '\n请查看日志：%s\n' "${IP_INSTALL_LOG:-本阶段未创建日志}" >&2
  exit "$code"
}
trap 'error_report $LINENO' ERR
die() { printf '安装停止：%s\n' "$*" >&2; exit 1; }
note() { printf '\n==> %s\n' "$*"; }
quiet() { "$@" >>"$IP_INSTALL_LOG" 2>&1; }
live() { "$@" 2>&1 | tee -a "$IP_INSTALL_LOG"; }
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( 1 <= 10#$1 && 10#$1 <= 65535 )); }
port_free() {
  local port=$1 proto=$2
  if [[ $proto == tcp ]]; then
    ! ss -ltnH "sport = :$port" | grep -q .
  else
    ! ss -lunH "sport = :$port" | grep -q .
  fi
}
pick_port() {
  local proto=$1 candidate
  for _ in {1..100}; do
    candidate="$(python3 -c 'import secrets; print(20000 + secrets.randbelow(35000))')"
    [[ " ${USED_PORTS[*]:-} " != *" $candidate "* ]] || continue
    port_free "$candidate" "$proto" || continue
    printf '%s' "$candidate"
    return 0
  done
  die "无法找到空闲的 $proto 端口。"
}
ask_port() {
  local name=$1 label=$2 proto=$3 answer
  answer=""
  if [[ "${IP_SETUP_MODE:-auto}" == manual ]]; then read -r -p "$label（回车随机选择）: " answer </dev/tty; fi
  if [[ -z "$answer" ]]; then answer="$(pick_port "$proto")"; fi
  valid_port "$answer" || die "$label 端口不是 1–65535 的整数。"
  [[ " ${USED_PORTS[*]:-} " != *" $answer "* ]] || die "$label 端口 $answer 与其他服务冲突。"
  port_free "$answer" "$proto" || die "$label 端口 $answer 已被占用。"
  printf -v "$name" '%s' "$answer"
  USED_PORTS+=("$answer")
  printf '%s：%s/%s\n' "$label" "$answer" "$proto" >>"$IP_INSTALL_LOG"
}

upgrade_panel() {
  local updater
  note '正在更新住宅面板并检查 HTTPS 证书…'
  updater="$(mktemp)"
  printf '%s' '__UPDATER_B64__' | base64 -d > "$updater"
  if ! IP_PUBLIC="$PUBLIC_IP" IP_LINK_TCP="$1" IP_LINK_UDP="$2" IP_LINK_HOME="$3" quiet bash "$updater"; then
    printf '面板更新未完成，详情见日志。\n' >&2
  fi
  rm -f "$updater"
}

save_links() {
  printf '%s\n%s\n%s\n' "$1" "$2" "$3" > /root/three-nodes-links.txt
  chmod 600 /root/three-nodes-links.txt
}

refresh_home_status() {
  HOME_IP=''
  [[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]] || return 0
  local values port user password attempt
  mapfile -t values < <(python3 - <<'PY'
import json,pathlib
p=pathlib.Path('/etc/residential-stack')
s=json.loads((p/'settings.json').read_text()); a=json.loads((p/'auth.json').read_text())
for value in (s['proxy_port'], a['socks_username'], a['socks_password']): print(value)
PY
)
  if [[ ${#values[@]} != 3 ]]; then
    HOME_STATUS='无法读取住宅代理配置，请查看本机 settings.json 和 auth.json'
    return 0
  fi
  port="${values[0]}"; user="${values[1]}"; password="${values[2]}"
  note '正在检查住宅出口，请稍候…'
  for attempt in {1..6}; do
    if ! systemctl is-active --quiet residential-agent.service; then
      HOME_STATUS='住宅代理服务未正常运行，请查看下方程序日志'
      return 0
    fi
    HOME_IP="$(curl -4fsS --max-time 8 --socks5-hostname "127.0.0.1:$port" \
      --proxy-user "$user:$password" https://api.ipify.org 2>/dev/null || true)"
    if [[ "$HOME_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      HOME_STATUS="当前住宅代理出口已连通：$HOME_IP"
      return 0
    fi
    [[ "$attempt" == 6 ]] || sleep 8
  done
  HOME_IP=''
  HOME_STATUS='住宅出口尚未连通；本机 SOCKS 出站检测失败，原因见下方程序及 OpenVPN 日志'
}

show_home_diagnostics() {
  printf '\n住宅代理程序日志（不是服务重启记录）：\n'
  if [[ -f /opt/residential-stack-agent/manager.log ]]; then
    tail -n 18 /opt/residential-stack-agent/manager.log
  else
    journalctl -u residential-agent.service -n 25 --no-pager -o cat 2>/dev/null || true
  fi
  if [[ -f /opt/residential-stack-agent/ovpn_err_0.log ]]; then
    printf '\n最近的上游 OpenVPN 连接日志：\n'
    tail -n 18 /opt/residential-stack-agent/ovpn_err_0.log
  fi
  printf '\n本检测经本机 SOCKS 访问外网，不经过公网入站节点或面板端口。\n'
}

[[ $EUID -eq 0 ]] || die '请用 root 或 sudo 运行。'
IP_INSTALL_LOG="$(mktemp /root/ip-install.XXXXXX.log)"
export IP_INSTALL_LOG
command -v apt-get >/dev/null || die '目前支持 Debian/Ubuntu。'
command -v systemctl >/dev/null || die '需要 systemd。'
case "$(uname -m)" in x86_64|aarch64) ;; *) die "不支持的 CPU 架构：$(uname -m)" ;; esac
if ! command -v python3 >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  note '准备运行环境…'
  quiet apt-get update -q
  quiet apt-get install -y python3
fi
[[ -r /dev/tty ]] || die '需要交互式 SSH 终端。'
if [[ -z "${IP_SETUP_MODE:-}" ]]; then
  printf '\n选择安装方式：\n  回车：全部自动安装\n  输入 2：手动填写参数\n'
  read -r -p '请选择（回车自动）: ' SETUP_CHOICE </dev/tty
  case "$SETUP_CHOICE" in ''|1) IP_SETUP_MODE=auto ;; 2) IP_SETUP_MODE=manual ;; *) die '请输入 2 或直接回车。' ;; esac
fi
case "$IP_SETUP_MODE" in auto|manual) ;; *) die '安装方式只能是 auto 或 manual。' ;; esac
export IP_SETUP_MODE
STAGE='检测并补全运行环境'
note "$STAGE"
ENVIRONMENT_TOOL="$(mktemp)"
ENVIRONMENT_REPORT="$(mktemp)"
printf '%s' '__PREFLIGHT_B64__' | base64 -d > "$ENVIRONMENT_TOOL"
live bash "$ENVIRONMENT_TOOL" "$ENVIRONMENT_REPORT"
rm -f "$ENVIRONMENT_TOOL"
STAGE='检查现有节点和面板'
DISCOVERY_TOOL="$(mktemp)"
printf '%s' '__DISCOVERY_B64__' | base64 -d > "$DISCOVERY_TOOL"
DISCOVERY_JSON="$(python3 "$DISCOVERY_TOOL")"
mapfile -t EXISTING < <(printf '%s' "$DISCOVERY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(d.get(k, "")) for k in ("tcp","udp","home","panel_url","panel_username","panel_password","ip","udp_cert")]')
EXIST_TCP="${EXISTING[0]:-}"; EXIST_UDP="${EXISTING[1]:-}"
EXIST_HOME="${EXISTING[2]:-}"; EXIST_PANEL="${EXISTING[3]:-}"
EXIST_PANEL_USER="${EXISTING[4]:-}"; EXIST_PANEL_PASS="${EXISTING[5]:-}"
PUBLIC_IP="${EXISTING[6]:-}"
export IP_HY2_CERT="${EXISTING[7]:-/etc/sing-box/hy2-cert.pem}"
[[ "$PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die '无法获得 VPS 公网 IPv4 地址。'
NETWORK_TOOL="$(mktemp)"
printf '%s' '__NETWORK_B64__' | base64 -d > "$NETWORK_TOOL"
mapfile -t NETWORK_SETUP < <(python3 "$NETWORK_TOOL" prepare)
[[ ${#NETWORK_SETUP[@]} == 2 ]] || die '网络类型检查失败。'
install -m 600 "$ENVIRONMENT_REPORT" "$STACK_DIR/environment-report.json"
rm -f "$ENVIRONMENT_REPORT"
IP_NETWORK_MODE="${NETWORK_SETUP[0]}"; IP_DISABLE_UDP="${NETWORK_SETUP[1]}"
export IP_NETWORK_MODE IP_DISABLE_UDP
if [[ "$IP_NETWORK_MODE" == mapped ]] || python3 -c 'import json; import sys; sys.exit(0 if json.load(open("/etc/residential-stack/network.json")).get("certificate_mode")=="domain" else 1)'; then export IP_SKIP_CERT=1; fi
finish_network() {
  if [[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]]; then
    install -m 600 "$NETWORK_TOOL" "$STACK_DIR/network-adapter.py"
    printf '%s' '__VERIFY_B64__' | base64 -d > "$STACK_DIR/verify-node.py"
    chmod 600 "$STACK_DIR/verify-node.py"
    NETWORK_RESULT=0
    python3 "$STACK_DIR/network-adapter.py" apply || NETWORK_RESULT=$?
  fi
  rm -f "$NETWORK_TOOL"
}
if [[ -n "$EXIST_TCP" && ( -n "$EXIST_UDP" || "$IP_DISABLE_UDP" == 1 ) && -n "$EXIST_HOME" && -n "$EXIST_PANEL" ]]; then
  if [[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]]; then
    upgrade_panel "$EXIST_TCP" "$EXIST_UDP" "$EXIST_HOME"
    refresh_home_status
  fi
  if [[ -f "$STACK_DIR/settings.json" ]]; then
    finish_network
    rm -f "$DISCOVERY_TOOL"
    exit "${NETWORK_RESULT:-0}"
  fi
  save_links "$EXIST_TCP" "$EXIST_UDP" "$EXIST_HOME"
  printf '\n三条节点链接（按 TCP、Hysteria2 UDP、家宽顺序；可整段复制导入）：\n\n'
  cat /root/three-nodes-links.txt
  printf '\n家宽面板：%s\n面板用户名：%s\n面板密码：%s\n' "$EXIST_PANEL" "$EXIST_PANEL_USER" "$EXIST_PANEL_PASS"
  printf '节点入口服务处于运行状态；家宽出口是否可用，请在面板中查看。\n'
  if [[ -n "${HOME_STATUS:-}" ]]; then
    printf '家宽出口状态：%s\n' "$HOME_STATUS"
    [[ -n "${HOME_IP:-}" ]] || quiet show_home_diagnostics
  fi
  rm -f "$DISCOVERY_TOOL"
  exit 0
fi
NEED_STACK=0
if [[ -z "$EXIST_HOME" || -z "$EXIST_PANEL" ]]; then NEED_STACK=1; fi
[[ -r /dev/tty ]] || die '需要交互式 SSH 终端完成缺失组件的安装。'
if [[ "$NEED_STACK" == 1 ]]; then
  [[ -c /dev/net/tun ]] || die '这台 VPS 没有 /dev/net/tun，无法建立住宅 OpenVPN 出口。'
  for path in "$PANEL_UNIT" "$UNIT"; do
    if [[ -e "$path" ]] && ! grep -q 'Residential\|Three proxy entries' "$path"; then
      die "检测到同名但不属于本脚本的服务 $path；不会覆盖。"
    fi
  done
fi

STAGE='确认节点参数'
USED_PORTS=()
PANEL_PORT=''; SOCKS_PORT=''; REALITY_PORT=''; HY2_PORT=''; HOME_PORT=''
COUNTRY='JP'
if [[ "$NEED_STACK" == 1 ]]; then
  if [[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]]; then
    mapfile -t SAVED < <(python3 - "$STACK_DIR" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]); s=json.loads((p/'settings.json').read_text()); a=json.loads((p/'auth.json').read_text())
for x in (s['panel_port'],s['proxy_port'],s['country'],a['username'],a['password'],a['socks_username'],a['socks_password']): print(x)
PY
)
    PANEL_PORT="${SAVED[0]}"; SOCKS_PORT="${SAVED[1]}"; COUNTRY="${SAVED[2]}"
    PANEL_USER="${SAVED[3]}"; PANEL_PASS="${SAVED[4]}"
    SOCKS_USER="${SAVED[5]}"; SOCKS_PASS="${SAVED[6]}"
    printf '沿用本机已保存的面板账号及端口。\n' >>"$IP_INSTALL_LOG"
  else
    ask_port PANEL_PORT '住宅管理面板 HTTPS' tcp
    ask_port SOCKS_PORT '住宅代理内部端口' tcp
    if [[ "$IP_SETUP_MODE" == manual ]]; then read -r -p '住宅出口国家代码（回车使用 JP）: ' COUNTRY </dev/tty; fi
    COUNTRY="${COUNTRY:-JP}"; COUNTRY="${COUNTRY^^}"
    [[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || die '国家代码必须为两个英文字母，如 US、JP。'
  fi
  ask_port HOME_PORT '家宽出口 VLESS + Reality' tcp
fi
if [[ -z "$EXIST_TCP" ]]; then ask_port REALITY_PORT 'TCP VLESS + Reality' tcp; fi
if [[ -z "$EXIST_UDP" && "$IP_DISABLE_UDP" != 1 ]]; then ask_port HY2_PORT 'UDP Hysteria2' udp; fi
SNI=''
if [[ "$IP_SETUP_MODE" == manual ]]; then read -r -p 'Reality 伪装域名（回车使用 www.amazon.com）: ' SNI </dev/tty; fi
SNI="${SNI:-www.amazon.com}"
[[ "$SNI" =~ ^[A-Za-z0-9.-]+$ && "$SNI" == *.* ]] || die '伪装域名格式无效。'
if [[ "$IP_SETUP_MODE" == manual ]]; then
  read -r -p '回车开始安装，输入 no 取消: ' CONFIRM </dev/tty
  [[ "$CONFIRM" != no ]] || die '已取消。'
fi

STAGE='部署独立住宅管理面板和代理'
if [[ "$NEED_STACK" == 1 ]]; then
note "$STAGE"
install -d -m 700 "$STACK_DIR" "$WORK_DIR/configs"
printf '%s' '__AGENT_B64__' | base64 -d > "$WORK_DIR/lite_manager.py"
printf '%s' '__PROXY_B64__' | base64 -d > "$WORK_DIR/proxy_server.py"
printf '%s' '__PANEL_B64__' | base64 -d > "$STACK_DIR/panel.py"
chmod 600 "$WORK_DIR/lite_manager.py" "$WORK_DIR/proxy_server.py" "$STACK_DIR/panel.py"
python3 -m py_compile "$WORK_DIR/lite_manager.py" "$WORK_DIR/proxy_server.py" "$STACK_DIR/panel.py"
PANEL_USER="${PANEL_USER:-adm_$(openssl rand -hex 4)}"
PANEL_PASS="${PANEL_PASS:-$(openssl rand -hex 18)}"
SOCKS_USER="${SOCKS_USER:-res_$(openssl rand -hex 4)}"
SOCKS_PASS="${SOCKS_PASS:-$(openssl rand -hex 18)}"
export STACK_DIR PANEL_PORT SOCKS_PORT COUNTRY PANEL_USER PANEL_PASS SOCKS_USER SOCKS_PASS
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path(os.environ['STACK_DIR'])
auth = {'username':os.environ['PANEL_USER'],'password':os.environ['PANEL_PASS'],
        'socks_username':os.environ['SOCKS_USER'],'socks_password':os.environ['SOCKS_PASS']}
settings = {'panel_port':int(os.environ['PANEL_PORT']),'proxy_port':int(os.environ['SOCKS_PORT']),
            'country':os.environ['COUNTRY'],'mode':'auto','manual_node_ip':'','switch_revision':0}
for name, value in [('auth.json',auth),('settings.json',settings)]:
    target = p / name
    if not target.exists():
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
        target.chmod(0o600)
PY
if [[ ! -f "$STACK_DIR/panel-cert.pem" || ! -f "$STACK_DIR/panel-key.pem" ]]; then
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
  -keyout "$STACK_DIR/panel-key.pem" -out "$STACK_DIR/panel-cert.pem" \
  -subj "/CN=$PUBLIC_IP" -addext "subjectAltName=IP:$PUBLIC_IP,IP:127.0.0.1" >/dev/null 2>&1
fi
chmod 600 "$STACK_DIR/panel-key.pem" "$STACK_DIR/panel-cert.pem"
PANEL_FINGERPRINT="$(openssl x509 -in "$STACK_DIR/panel-cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
cat > "$PANEL_UNIT" <<'UNIT'
[Unit]
Description=Independent Residential Exit Management Panel
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /etc/residential-stack/panel.py
Restart=always
RestartSec=5
ProtectHome=yes

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/residential-agent.service <<'UNIT'
[Unit]
Description=Residential Proxy Core Engine
After=network.target residential-panel.service
Requires=residential-panel.service

[Service]
Type=simple
WorkingDirectory=/opt/residential-stack-agent
ExecStart=/usr/bin/python3 -u /opt/residential-stack-agent/lite_manager.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
quiet systemctl daemon-reload
live systemctl enable --now residential-panel.service
PANEL_READY=0
for _ in {1..10}; do
  if curl -ksSf --max-time 3 -u "$PANEL_USER:$PANEL_PASS" "https://127.0.0.1:$PANEL_PORT/api/config" >/dev/null 2>&1; then
    PANEL_READY=1
    break
  fi
  sleep 1
done
[[ "$PANEL_READY" == 1 ]] || die '独立住宅面板 API 验证失败；请查看 residential-panel.service 日志。'
live systemctl enable --now residential-agent.service
for _ in {1..20}; do
  port_free "$SOCKS_PORT" tcp || break
  sleep 1
done
port_free "$SOCKS_PORT" tcp && die '住宅代理内部端口未监听；请查看 residential-agent.service 日志。'
fi

STAGE='安装缺少的 sing-box 节点'
note "$STAGE"
install -d -m 700 /etc/sing-box /opt/residential-stack-bin
SING_BOX='/opt/residential-stack-bin/sing-box'
if [[ ! -x "$SING_BOX" ]]; then
  case "$(uname -m)" in x86_64) SB_ARCH=amd64 ;; aarch64) SB_ARCH=arm64 ;; esac
  ARCHIVE="$(mktemp)"
  RELEASE="https://github.com/SagerNet/sing-box/releases/download/v1.14.1/sing-box-1.14.1-linux-${SB_ARCH}.tar.gz"
  printf '正在下载节点程序，完成后校验官方文件摘要…\n'
  if live curl -fL --retry 3 --connect-timeout 15 --max-time 120 "$RELEASE" -o "$ARCHIVE"; then
    if python3 - "$ARCHIVE" "$SB_ARCH" <<'PY'
import hashlib,json,pathlib,sys,urllib.request
try:
    req=urllib.request.Request('https://api.github.com/repos/SagerNet/sing-box/releases/tags/v1.14.1',headers={'User-Agent':'ip-installer'})
    with urllib.request.urlopen(req,timeout=25) as r: release=json.load(r)
    name='sing-box-1.14.1-linux-'+sys.argv[2]+'.tar.gz'
    digest=next(a for a in release['assets'] if a['name']==name).get('digest','')
    if not digest.startswith('sha256:') or hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()!=digest[7:]:
        raise ValueError('校验失败')
except Exception:
    print('节点程序下载未通过官方 SHA256 校验，不会执行该下载文件。',file=sys.stderr)
    raise SystemExit(1)
PY
    then
      tar -xzf "$ARCHIVE" -C /opt/residential-stack-bin --strip-components=1 "sing-box-1.14.1-linux-${SB_ARCH}/sing-box"
      chmod 700 "$SING_BOX"
    fi
  fi
  rm -f "$ARCHIVE"
fi
if [[ ! -x "$SING_BOX" ]]; then
  SING_BOX="$(command -v sing-box || true)"
  if [[ -z "$SING_BOX" && -x /etc/s-box/sing-box ]]; then SING_BOX='/etc/s-box/sing-box'; fi
fi
[[ -n "$SING_BOX" && -x "$SING_BOX" ]] || die '无法获取 sing-box，且未找到可复用的本机可执行文件。'
if [[ "$NEED_STACK" == 1 ]]; then
  SING_BOX="$SING_BOX" python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path('/etc/residential-stack/settings.json')
s = json.loads(p.read_text(encoding='utf-8'))
s['sing_box_bin'] = os.environ['SING_BOX']
p.write_text(json.dumps(s, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
p.chmod(0o600)
PY
fi
pair_one="$($SING_BOX generate reality-keypair)"
pair_home="$($SING_BOX generate reality-keypair)"
REALITY_PRIVATE="$(printf '%s\n' "$pair_one" | sed -n 's/^PrivateKey: *//p' | head -1)"
REALITY_PUBLIC="$(printf '%s\n' "$pair_one" | sed -n 's/^PublicKey: *//p' | head -1)"
HOME_PRIVATE="$(printf '%s\n' "$pair_home" | sed -n 's/^PrivateKey: *//p' | head -1)"
HOME_PUBLIC="$(printf '%s\n' "$pair_home" | sed -n 's/^PublicKey: *//p' | head -1)"
[[ -n "$REALITY_PRIVATE" && -n "$REALITY_PUBLIC" && -n "$HOME_PRIVATE" && -n "$HOME_PUBLIC" ]] || die 'Reality 密钥生成失败。'
REALITY_UUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
HOME_UUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
REALITY_SID="$(openssl rand -hex 4)"
HOME_SID="$(openssl rand -hex 4)"
HY2_PASS="$(openssl rand -hex 20)"
HY2_PIN=''
if [[ -z "$EXIST_UDP" && "$IP_DISABLE_UDP" != 1 ]]; then
  if [[ ! -f /etc/sing-box/hy2-cert.pem || ! -f /etc/sing-box/hy2-key.pem ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
      -keyout /etc/sing-box/hy2-key.pem -out /etc/sing-box/hy2-cert.pem \
      -subj "/CN=$SNI" -addext "subjectAltName=DNS:$SNI,IP:$PUBLIC_IP" >/dev/null 2>&1
  fi
  chmod 600 /etc/sing-box/hy2-key.pem /etc/sing-box/hy2-cert.pem
  HY2_PIN="$(openssl x509 -in /etc/sing-box/hy2-cert.pem -noout -fingerprint -sha256 | cut -d= -f2 | tr -d :)"
fi
export CONFIG REALITY_PORT HY2_PORT HOME_PORT SOCKS_PORT SOCKS_USER SOCKS_PASS SNI REALITY_PRIVATE REALITY_PUBLIC HOME_PRIVATE HOME_PUBLIC REALITY_UUID HOME_UUID REALITY_SID HOME_SID HY2_PASS EXIST_TCP EXIST_UDP NEED_STACK IP_DISABLE_UDP
python3 - <<'PY'
import json, os, pathlib
o = os.environ
path = pathlib.Path(o['CONFIG'])
if path.exists():
    config = json.loads(path.read_text(encoding='utf-8'))
else:
    config = {'log':{'level':'warn'}, 'inbounds':[], 'outbounds':[{'type':'direct','tag':'direct'}], 'route':{'rules':[], 'final':'direct'}}
inbounds = config.setdefault('inbounds', [])
outbounds = config.setdefault('outbounds', [])
route = config.setdefault('route', {'rules': [], 'final': 'direct'})
tags = {item.get('tag') for item in inbounds}
def reality(tag, port, uuid, key, sid):
    return {'type':'vless','tag':tag,'listen':'0.0.0.0','listen_port':int(o[port]),
      'users':[{'uuid':o[uuid],'flow':'xtls-rprx-vision'}],
      'tls':{'enabled':True,'server_name':o['SNI'],
        'reality':{'enabled':True,'handshake':{'server':o['SNI'],'server_port':443},
          'private_key':o[key],'short_id':[o[sid]]}}}
if not o['EXIST_TCP'] and 'reality-direct' not in tags:
    inbounds.append(reality('reality-direct','REALITY_PORT','REALITY_UUID','REALITY_PRIVATE','REALITY_SID'))
if not o['EXIST_UDP'] and o.get('IP_DISABLE_UDP') != '1' and 'hy2-direct' not in tags:
    inbounds.append({'type':'hysteria2','tag':'hy2-direct','listen':'0.0.0.0','listen_port':int(o['HY2_PORT']),
      'users':[{'password':o['HY2_PASS']}],
      'tls':{'enabled':True,'certificate_path':'/etc/sing-box/hy2-cert.pem','key_path':'/etc/sing-box/hy2-key.pem'},
      'masquerade':{'type':'proxy','url':'https://'+o['SNI'],'rewrite_host':True}})
if o['NEED_STACK'] == '1':
    if 'reality-home' not in tags:
        inbounds.append(reality('reality-home','HOME_PORT','HOME_UUID','HOME_PRIVATE','HOME_SID'))
    home_out = next((item for item in outbounds if item.get('tag') == 'home'), None)
    home_value = {'type':'socks','tag':'home','server':'127.0.0.1','server_port':int(o['SOCKS_PORT']),
                  'version':'5','username':o['SOCKS_USER'],'password':o['SOCKS_PASS']}
    if home_out is None:
        outbounds.append(home_value)
    else:
        home_out.update(home_value)
    if not any('reality-home' in item.get('inbound', []) for item in route.setdefault('rules', [])):
        route['rules'].insert(0, {'inbound':['reality-home'],'outbound':'home'})
target = path.with_name(path.name + '.new')
target.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
target.chmod(0o600)
PY
"$SING_BOX" check -c "$CONFIG.new" >/dev/null || die 'sing-box 配置检查失败；已有节点配置没有被修改。'
mv -f "$CONFIG.new" "$CONFIG"
cat > "$UNIT" <<UNIT
[Unit]
Description=Three proxy entries (VLESS Reality, Hysteria2, residential)
After=network.target residential-agent.service
Wants=network.target

[Service]
User=root
ExecStart=$SING_BOX run -c $CONFIG
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT
quiet systemctl daemon-reload
quiet systemctl enable three-nodes.service
quiet systemctl restart three-nodes.service
sleep 2
[[ "$(systemctl is-active three-nodes.service)" == active ]] || die '补建节点服务没有启动。'
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
  [[ -z "$REALITY_PORT" ]] || ufw allow "$REALITY_PORT/tcp" >/dev/null
  [[ -z "$HOME_PORT" ]] || ufw allow "$HOME_PORT/tcp" >/dev/null
  [[ -z "$PANEL_PORT" ]] || ufw allow "$PANEL_PORT/tcp" >/dev/null
  [[ -z "$HY2_PORT" ]] || ufw allow "$HY2_PORT/udp" >/dev/null
fi

STAGE='验证节点与住宅出口'
note "$STAGE"
smoke_node() {
  SMOKE_KIND="$1" SING_BOX="$SING_BOX" python3 - <<'PY'
import json, os, pathlib, socket, subprocess, tempfile, time
o = os.environ
kind = o['SMOKE_KIND']
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    local_port = s.getsockname()[1]
if kind in ('reality', 'home'):
    is_home = kind == 'home'
    outbound = {'type':'vless','tag':'proxy','server':'127.0.0.1',
      'server_port':int(o['HOME_PORT'] if is_home else o['REALITY_PORT']),
      'uuid':o['HOME_UUID'] if is_home else o['REALITY_UUID'],
      'flow':'xtls-rprx-vision','tls':{'enabled':True,'server_name':o['SNI'],
        'utls':{'enabled':True,'fingerprint':'chrome'},
        'reality':{'enabled':True,
          'public_key':o['HOME_PUBLIC'] if is_home else o['REALITY_PUBLIC'],
          'short_id':o['HOME_SID'] if is_home else o['REALITY_SID']}}}
else:
    outbound = {'type':'hysteria2','tag':'proxy','server':'127.0.0.1',
      'server_port':int(o['HY2_PORT']),'password':o['HY2_PASS'],
      'tls':{'enabled':True,'server_name':o['SNI'],'insecure':True}}
config = {'log':{'level':'warn'},
  'inbounds':[{'type':'socks','tag':'local','listen':'127.0.0.1','listen_port':local_port}],
  'outbounds':[outbound], 'route':{'final':'proxy'}}
with tempfile.TemporaryDirectory(prefix='three-nodes-smoke-') as d:
    path = pathlib.Path(d) / 'client.json'
    log_path = pathlib.Path(d) / 'client.log'
    path.write_text(json.dumps(config), encoding='utf-8')
    subprocess.run([o['SING_BOX'],'check','-c',str(path)],check=True,stdout=subprocess.DEVNULL)
    with log_path.open('wb') as log:
        client = subprocess.Popen([o['SING_BOX'],'run','-c',str(path)],stdout=log,stderr=subprocess.STDOUT)
        try:
            time.sleep(1)
            if client.poll() is not None:
                raise RuntimeError('客户端测试进程未启动')
            attempts = []
            for url in ('https://api.ipify.org','http://ifconfig.me/ip'):
                test = subprocess.run(['curl','-4fsS','--max-time','12','--socks5-hostname',
                    f'127.0.0.1:{local_port}',url],capture_output=True,text=True)
                ip = test.stdout.strip()
                if test.returncode == 0 and ip.count('.') == 3:
                    print(f'{kind} 本机握手及出口测试通过：{ip}')
                    break
                attempts.append(f'{url}: curl 退出码 {test.returncode}, {test.stderr.strip()[:160]}')
            else:
                raise RuntimeError('; '.join(attempts))
        except Exception as e:
            print(f'{kind} 节点验证失败：{e}',flush=True)
            print(log_path.read_text(errors='replace')[-1200:],flush=True)
            raise
        finally:
            client.terminate()
            client.wait(timeout=5)
PY
}
NODE_REALITY="$EXIST_TCP"
NODE_HY2="$EXIST_UDP"
NODE_HOME="$EXIST_HOME"
DIRECT_STATUS='沿用运行中的现有节点；未修改其配置'
HY2_STATUS='沿用运行中的现有节点；未修改其配置'
HOME_STATUS='沿用运行中的现有家宽节点；请在原面板确认出口状态'
PANEL_URL="$EXIST_PANEL"
if [[ "$NEED_STACK" == 0 ]]; then
  PANEL_USER="$EXIST_PANEL_USER"
  PANEL_PASS="$EXIST_PANEL_PASS"
fi
PANEL_FINGERPRINT='现有面板'
if [[ -z "$EXIST_TCP" ]]; then
  NODE_REALITY="vless://${REALITY_UUID}@${PUBLIC_IP}:${REALITY_PORT}?encryption=none&security=reality&sni=${SNI}&fp=chrome&pbk=${REALITY_PUBLIC}&sid=${REALITY_SID}&type=tcp&flow=xtls-rprx-vision#VLESS-Reality-TCP"
  port_free "$REALITY_PORT" tcp && die '新建 VLESS Reality TCP 端口没有监听。'
  if quiet smoke_node reality; then DIRECT_STATUS='新建节点本机握手及出口测试通过；公网端口需从外部验证'
  else DIRECT_STATUS='节点已生成，本机检测未通过；请查看安装日志'; fi
fi
if [[ -z "$EXIST_UDP" && "$IP_DISABLE_UDP" != 1 ]]; then
  NODE_HY2="hysteria2://${HY2_PASS}@${PUBLIC_IP}:${HY2_PORT}/?sni=${SNI}&insecure=1&pinSHA256=${HY2_PIN}#Hysteria2-UDP"
  port_free "$HY2_PORT" udp && die '新建 Hysteria2 UDP 端口没有监听。'
  if quiet smoke_node hy2; then HY2_STATUS='新建节点本机 UDP 握手及出口测试通过；云平台 UDP 放行需从外部验证'
  else HY2_STATUS='Hysteria2 已生成，本机检测未通过；请查看安装日志'; fi
fi
HOME_IP=''
if [[ "$NEED_STACK" == 1 ]]; then
  NODE_HOME="vless://${HOME_UUID}@${PUBLIC_IP}:${HOME_PORT}?encryption=none&security=reality&sni=${SNI}&fp=chrome&pbk=${HOME_PUBLIC}&sid=${HOME_SID}&type=tcp&flow=xtls-rprx-vision#Residential-Exit"
  PANEL_URL="https://$PUBLIC_IP:$PANEL_PORT/"
  PANEL_FINGERPRINT="$(openssl x509 -in "$STACK_DIR/panel-cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
  port_free "$HOME_PORT" tcp && die '新建家宽入口 TCP 端口没有监听。'
fi
FINAL_DISCOVERY_JSON="$(python3 "$DISCOVERY_TOOL")"
mapfile -t MANAGED < <(printf '%s' "$FINAL_DISCOVERY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(d.get(k, "")) for k in ("managed_tcp","managed_udp","managed_home")]')
if [[ -z "$EXIST_TCP" && -n "${MANAGED[0]:-}" ]]; then NODE_REALITY="${MANAGED[0]}"; fi
if [[ -z "$EXIST_UDP" && -n "${MANAGED[1]:-}" ]]; then NODE_HY2="${MANAGED[1]}"; fi
if [[ "$NEED_STACK" == 1 && -n "${MANAGED[2]:-}" ]]; then NODE_HOME="${MANAGED[2]}"; fi
if [[ -f "$STACK_DIR/settings.json" && -f "$STACK_DIR/auth.json" ]]; then
  upgrade_panel "$NODE_REALITY" "$NODE_HY2" "$NODE_HOME"
  refresh_home_status
  if [[ "$NEED_STACK" == 1 && -n "$HOME_IP" ]]; then
    if ! quiet smoke_node home; then
      HOME_STATUS="上游出口 $HOME_IP 已连通，但家宽节点入口握手检测未通过，请查看上方错误"
    fi
  fi
fi
if [[ -f "$STACK_DIR/settings.json" ]]; then
  finish_network
  rm -f "$DISCOVERY_TOOL"
  exit "${NETWORK_RESULT:-0}"
fi
save_links "$NODE_REALITY" "$NODE_HY2" "$NODE_HOME"
cat > "$RESULT" <<INFO
$NODE_REALITY
$NODE_HY2
$NODE_HOME

上方三行可一起复制导入，顺序：TCP Reality、Hysteria2 UDP、家宽出口。

TCP 节点状态：$DIRECT_STATUS
Hysteria2 状态：$HY2_STATUS
家宽出口状态：$HOME_STATUS

住宅管理面板：$PANEL_URL
面板用户名：$PANEL_USER
面板密码：$PANEL_PASS
INFO
chmod 600 "$RESULT"
note "安装完成。信息已保存到 $RESULT（仅 root 可读）"
printf '\n三条节点链接（可整段复制导入）：\n\n'
cat "$RESULT"
printf '\n新建的公网端口需在云平台放行：'
[[ -z "$REALITY_PORT" ]] || printf ' TCP %s' "$REALITY_PORT"
[[ -z "$HOME_PORT" ]] || printf ' TCP %s' "$HOME_PORT"
[[ -z "$PANEL_PORT" ]] || printf ' TCP %s' "$PANEL_PORT"
[[ -z "$HY2_PORT" ]] || printf ' UDP %s' "$HY2_PORT"
printf '\n'
if [[ -f "$STACK_DIR/settings.json" && -z "$HOME_IP" ]]; then
  quiet show_home_diagnostics
fi
rm -f "$DISCOVERY_TOOL"
