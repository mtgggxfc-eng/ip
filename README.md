# ip：独立节点与住宅出口部署

> 2026-10-18 试用版：安装器、汇总后台与维护源码同步发布。
> 汇总后台已完成真实 HTTPS、D1、授权与 CF 资源生命周期验证；新版完整 VPS 安装、NVIDIA 映射 API 和用户网络验收仍待测试。

支持 Debian / Ubuntu、systemd、x86_64 或 aarch64；住宅出口需要 `/dev/net/tun`。

短安装命令（自动判断 root / sudo）：

```bash
curl -fsSL https://ip.aizoudd.ggff.net | bash
```

原始 GitHub 命令仍可用，短入口无法连接时可使用：

```bash
curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/jkyj.sh | { if [ "$(id -u)" -eq 0 ]; then bash; else sudo bash; fi; }
```

**直接回车自动安装；输入 `2` 手动填写。** 自动模式不再询问各端口，使用随机空闲端口、`JP`、`www.amazon.com`。已有配置及已选国家会复用。短命令是公共安装入口，不附带私人汇总授权；需加入汇总时仍使用面板签发的完整专属命令。短入口源码和维护见 [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)。

首次缺少外部条件时先保留配置并提示；补齐后重跑、按回车自动续装并更新输出。普通端口放行可以直接复检；随机映射需要平台接口或一次性填写脚本生成的 `/etc/residential-stack/port-mappings.json`，不能在没有来源的情况下自动推算。UUID/密码保留，客户端静态链接需重新导入。

| 环境 | 第一条 | 第二条 | 第三条 |
|---|---|---|---|
| 公网端口可用 | VLESS Reality TCP | Hysteria2 UDP | 住宅出口 Reality |
| 直连不可用，已授权 CF 隧道 | VLESS WebSocket TLS | Trojan WebSocket TLS，TCP 替代 | 住宅出口 VLESS WebSocket TLS |

优先选择通过检测的直连；没有 UDP 时，不用另一台 VPS 中转。节点与隧道程序均在本机运行，CF 是隧道服务提供方。

复制区保留已生成的节点，允许先安装再开放端口；没有映射地址时也显示原始链接，并明确要求替换为平台给出的外部地址/端口，不当作可用公网入口。未创建的协议不会凑数，可能少于三条。终端仅显示结果、链接、面板凭据和下一步；CF 隧道可用时仍用一行提示可选的备用直连端口。详细检测保存到 root 私有日志。自测成功不保证用户网络可达或长期稳定。

## 面板与自动化

- 普通 VPS 优先申请公网 IP 证书；没有公网 80 的映射机器优先使用独立 CF 隧道。
- 隧道失败且存在有效公网 TCP 映射及 DNS 授权时，尝试域名 DNS 验证证书作为备用。
- 自动模式使用已保存的授权或环境变量。新机器无法自动读取维护者电脑的密钥；缺授权会明确提示，公开仓库不提供私人令牌。
- 每台机器有独立隧道、凭据和状态。独立面板保留原页面布局与功能。
- 汇总面板是可选组件，需要管理员签发的一次性接入码。公共脚本没有默认私人汇总地址。
- 私人模式由汇总后台代建本机独立隧道/子域名，VPS 只拿自身运行凭据，不拿 CF 管理 API 令牌。
- 私人用户先登录自己的汇总面板，点击“新增机器”，复制 15 分钟有效的专属安装命令；普通公开命令不会加入私人后台。
- 选择独立机器后可点击“删除所选机器”：确认后撤销授权并清理该机专属隧道/DNS，关联入口会断开；不会卸载 VPS 程序。旧兼容分组禁止整组删除。
- 尝试系统防火墙放行；NVIDIA 平台有 API 授权及明确接口编号时，可自动建立 TCP 映射。缺少权限或不支持的平台会输出后台操作提示。

详见 [网络与自动化](docs/NETWORK.md)、[汇总面板与安全](docs/HUB.md)。

## 查看与卸载

```bash
sudo cat /root/three-nodes-info.txt
sudo cat /root/three-nodes-links.txt
```

```bash
curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/xiezai.sh | { if [ "$(id -u)" -eq 0 ]; then bash; else sudo bash; fi; }
```

先预览：保存脚本后执行 `sudo bash xiezai.sh --dry-run`。卸载只处理能确认归属的本项目组件，保留原有 sing-box、x-ui、proxy-lite、共享证书、系统包和防火墙规则。新版也核对并清理本项目创建的云资源；失败时保留授权，便于重试。`--keep-cloudflare` 可保留云端资源和清理资料。

## 给维护者和 AI

先读 [AGENTS.md](AGENTS.md) 和 [维护手册](docs/MAINTENANCE.md)。入口模板是 `src/install.sh`，不要手改生成脚本里的 Base64。
发布严格使用 release-files.json 清单；制品目录可能有以前生成的私人二维码与链接，不能整目录上传。

```bash
python scripts/build.py
python scripts/check.py
```

- [架构、文件职责、接口和运行路径](docs/ARCHITECTURE.md)
- [网络模式、证书、端口、环境变量](docs/NETWORK.md)
- [统一面板、权限、部署和迁移](docs/HUB.md)
- [构建、升级、回滚、故障处理](docs/MAINTENANCE.md)
- [测试证据与尚未验证项目](docs/TESTING.md)
- [变更记录](CHANGELOG.md)

住宅选择逻辑源自用户原有部署，上游项目：[Free-Residential-IP-Proxy-Controller](https://github.com/a6216abcd/Free-Residential-IP-Proxy-Controller)。候选来自 VPNGate；“住宅”名称不保证 IP 类型、信誉或地理位置。
