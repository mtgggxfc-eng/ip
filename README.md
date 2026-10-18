# ip：家宽节点一键部署

在 Debian/Ubuntu VPS 的 SSH 终端执行：

```bash
curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/jkyj.sh | { if [ "$(id -u)" -eq 0 ]; then bash; else sudo bash; fi; }
```

卸载本脚本创建的组件，方便重新测试安装：

```bash
curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/xiezai.sh | { if [ "$(id -u)" -eq 0 ]; then bash; else sudo bash; fi; }
```

卸载会删除本脚本创建的节点配置、独立面板、代理、面板证书与续期任务，以及保存的节点信息。原有的 sing-box、x-ui、proxy-lite 服务、系统依赖和防火墙规则保留；可能被其他服务复用的 Hysteria2 证书也会保留。发现同名但无法确认归属的服务或配置时，会先停止卸载并说明原因。卸载后重新安装，已删除组件的账号与节点密钥会重新生成。

只预览卸载范围时，使用 root 执行 `bash <(curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/xiezai.sh) --dry-run`。

脚本会检查**正在运行的 sing-box 配置**：能恢复的 VLESS + Reality TCP、Hysteria2 UDP、住宅出口节点直接输出；缺少的节点再补建。住宅管理面板与出口缺失时，在 VPS 上部署独立面板和 OpenVPN 住宅代理。重复执行会打印已有信息，不因为系统装过 sing-box 就停止。

安装时需要交互式 SSH 终端。提示端口时直接回车会随机选择空闲端口；住宅出口默认国家 `US`，Reality 默认伪装域名 `www.amazon.com`。完成后三条纯节点链接会连续显示，可一起复制导入 v2rayN。链接另存为 `/root/three-nodes-links.txt`；新部署的面板信息另存为 `/root/three-nodes-info.txt`，仅 root 可读。

面板复用现有原版的页面和操作布局，保留自动/手动节点、国家、端口、强制换 IP、YouTube 检测、轮询间隔、候选节点和实时日志等功能。每台 VPS 独立管理。再次执行同一条命令，会更新本脚本安装的面板，保留节点链接和登录信息。

脚本会自动申请 Let’s Encrypt 公网 IP 正式证书，并每 12 小时检查续期，无需准备域名。需要系统 Python 3.10 以上，且公网 **TCP 80** 可到达 VPS、未被其他程序占用；本机 UFW 若启用会自动放行。云平台安全列表仍需放行 80。失败时终端会显示具体错误，面板暂时保留临时证书、节点继续使用；排除原因后再次执行即可重试。正式证书加载后，请关闭旧浏览器页面并重新打开。参考 [Let’s Encrypt IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。

住宅出口来自 VPNGate 上游节点，是否能取得指定国家的可用住宅 IP 取决于上游供应；脚本会显示出口状态和失败日志。

本脚本独立使用 `residential-panel.service`、`residential-agent.service`、`three-nodes.service`、`/opt/residential-stack-agent` 和 `tun90`，不覆盖已有 sing-box 服务或旧住宅代理。若主机只有 x-ui 节点且没有可读的活动 sing-box 配置，脚本会补建独立节点。云平台的安全列表仍需放行新建的公网 TCP/UDP 端口；本机 UFW 若已启用，脚本会自动添加规则。

住宅代理的节点选择逻辑改编自用户现有住宅面板部署，原项目见 [Free-Residential-IP-Proxy-Controller](https://github.com/a6216abcd/Free-Residential-IP-Proxy-Controller)。安装脚本和仓库不含面板账号、代理密码、Cloudflare Token 或 VPS 私钥；新部署的凭据在 VPS 上随机生成。
