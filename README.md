# ip：家宽节点一键部署

在 Debian/Ubuntu VPS 的 SSH 终端执行：

```bash
curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/jkyj.sh | { if [ "$(id -u)" -eq 0 ]; then bash; else sudo bash; fi; }
```

脚本会检查**正在运行的 sing-box 配置**：能恢复的 VLESS + Reality TCP、Hysteria2 UDP、住宅出口节点直接输出；缺少的节点再补建。住宅管理面板与出口缺失时，在 VPS 上部署独立面板和 OpenVPN 住宅代理。重复执行会打印已有信息，不因为系统装过 sing-box 就停止。

安装时需要交互式 SSH 终端。提示端口时直接回车会随机选择空闲端口；住宅出口默认国家 `US`，Reality 默认伪装域名 `www.amazon.com`。完成后终端显示三条节点链接、面板地址、用户名和密码；新部署的信息另存为 `/root/three-nodes-info.txt`，仅 root 可读。

独立面板可修改这台 VPS 的国家、代理内部端口、指定出口 IP，或请求更换出口。面板使用随机账号密码和自签名 HTTPS 证书。首次打开需核对安装输出的证书指纹。住宅出口来自 VPNGate 上游节点，是否能取得指定国家的可用住宅 IP 取决于上游供应；脚本会显示出口状态和失败日志。

本脚本独立使用 `residential-panel.service`、`residential-agent.service`、`three-nodes.service`、`/opt/residential-stack-agent` 和 `tun90`，不覆盖已有 sing-box 服务或旧住宅代理。若主机只有 x-ui 节点且没有可读的活动 sing-box 配置，脚本会补建独立节点。云平台的安全列表仍需放行新建的公网 TCP/UDP 端口；本机 UFW 若已启用，脚本会自动添加规则。

住宅代理的节点选择逻辑改编自用户现有住宅面板部署，原项目见 [Free-Residential-IP-Proxy-Controller](https://github.com/a6216abcd/Free-Residential-IP-Proxy-Controller)。安装脚本和仓库不含面板账号、代理密码、Cloudflare Token 或 VPS 私钥；新部署的凭据在 VPS 上随机生成。
