# 网络、端口与自动化

## 1. 安装入口

第一次只显示一个菜单：回车自动；2 手动。可以预先设置 IP_SETUP_MODE=auto/manual 省略这个菜单。自动模式的 ask() 不读终端，不会继续询问参数。

自动使用随机空闲端口、住宅国家 US、Reality 域名 www.amazon.com。手动模式能填写这些参数，以及映射、CF 令牌和域名。默认采用原生 Reality/Hysteria2，不修改用户其他服务。

## 2. 环境判断与连接优先级

prepare() 检查已有 network.json。新实例的默认路由源地址为 link-local 时，默认按映射环境处理，适配 NVIDIA Air。普通云 VPS 即使网卡是私网地址，也不能仅凭私网 IP 就判定为这种映射机器。

这不是万能平台识别。无法准确推断时使用普通模式；通过公开自测发现入口失败后尝试已授权的隧道。用户也可显式设置 IP_NETWORK_MODE。

每个角色依次尝试：

1. 已有或新建的原生入口；映射环境必须替换为真实外部地址和端口。
2. 同机独立 CF 隧道入口。
3. 都失败则不进入最终复制区，保留候选和错误说明。

第二个角色的键仍叫 udp，用于兼容已有 links.json；该角色可能选中 Trojan WebSocket TCP。查看 URI 协议和终端真实标签，不能按角色键判断协议。

只在 127.0.0.1 监听的 WebSocket 后端不需要公网入站。CF 边缘到本机由 cloudflared 建立加密隧道，客户端到 CF 使用 HTTPS/WSS 并验证正式证书。没有导出 insecure=1 的 WS 链接。

## 3. 为什么不把 UDP 装好就算完成

Hysteria2 能在本机握手，不证明平台会转发公网 UDP。NVIDIA Services 的 HTTP/HTTPS/Other/SSH 是服务类型，其公开创建接口没有 UDP 协议字段；不把 Other 当成 UDP 支持。

无 UDP 的默认替代是同机 Trojan WebSocket TLS，不使用其他 VPS。Hysteria Realms 需要双端支持、UDP 出站、可打洞 NAT；不作为默认分享链接。Mimic 需要双端 Linux/eBPF，也不符合普通 Windows v2rayN 一条链接导入的目标。

CF 隧道不是专线。延迟、速度、地区可达性、CF 服务条款和上游出口都会影响体验。两个不同 WS 协议共用同一隧道，并不构成两条独立物理线路。

参考：[NVIDIA 服务 API](https://docs.nvidia.com/dsx-air/api-reference/services/api-v-3-simulations-nodes-interfaces-services-create/)、[Hysteria Realms](https://v2.hysteria.network/docs/advanced/Realms/)、[Mimic](https://v2.hysteria.network/docs/advanced/Mimic/)。

## 4. 面板证书

| 条件 | 处理 |
|---|---|
| 普通公网，公网 TCP 80 可到达 | Certbot 5.4.0，IP shortlived 证书；12 小时检查 |
| 映射机器，已配置 CF 授权 | 独立隧道和独立 panel 子域名；浏览器使用 CF 边缘证书 |
| 隧道失败，已知有效面板映射 | 独立 direct 子域名、DNS-01 证书、外部端口 |
| 两条路都缺条件 | 不输出“面板可用”，打印缺少的授权或映射 |

HTTP-01 只能验证公网 80；内部 80 映射到公网随机端口不满足条件。DNS-01 不需要公网 80，但需要自己的域名和 DNS 授权，不能申请 IP 证书。DNS 证书用 Certbot/dns-cloudflare 5.4.0、独立配置目录。域名模式保留续期脚本，避免再次执行被 IP 证书脚本覆盖。

自动切换目前发生在安装/再次执行时，并非持续的跨入口故障切换服务。独立隧道会自行重连；没有承诺同一个浏览器 URL 可在任意 CF 故障时透明切到直连。CF API 或同一 DNS 提供商故障时，两种方式可能同时受影响。

参考：[验证方式](https://letsencrypt.org/docs/challenge-types/)、[IP 证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)、[CF 连接检测](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/connectivity-prechecks/)。

## 5. 防火墙与云平台

先处理本机防火墙：

1. UFW 正在启用时，添加指定协议/端口规则。
2. 否则，firewalld 正在运行时添加运行及永久规则。
3. 否则，使用 iptables/ip6tables 添加带 ip-installer 注释的 ACCEPT 规则；先检查是否已存在。
4. 不清空规则、不更改默认策略。用自己的 oneshot 服务在开机时重放。

本机命令成功不等于云端已经放行；nftables 特殊链、云安全组、多层防火墙仍可能拦截。公开入口实测是另一项证据。

NVIDIA 平台：仅当有 API Bearer 授权和明确的 interface UUID，才通过官方 API 查询/新增 TCP 服务。复用已有同接口同内部端口的服务，不修改它；只记录和清理自己新建的服务。没有 UDP 字段，因此不自动创建所谓 UDP Other 服务。缺少接口编号时，不猜测或改动账号里其他模拟环境。

其他厂商目前没有通用安全组写入适配。没有平台授权时，终端列出“协议 + 内部端口 + 需要后台放行/映射”，不能声称已自动开云端端口。普通 VPS 需要开放的是入站；住宅连接还需要 DNS/HTTPS 和候选 OpenVPN 指定端口的出站。未限制出站时无须额外操作。

## 6. 环境变量与授权文件

通常只需要菜单。自动模式高级配置如下，敏感值应在私有终端或私有配置中提供，不写入命令截图、仓库或共享日志。sudo 可能清除环境变量，应以 root 或明确保留所需变量运行。

| 变量 | 作用 |
|---|---|
| IP_SETUP_MODE | auto / manual |
| IP_NETWORK_MODE | direct / mapped；覆盖网络模式 |
| IP_UDP_AVAILABLE | 0 / 1，平台 UDP 能力显式声明；不替代实测 |
| IP_CF_API_TOKEN | 对指定域名 DNS 编辑、区域读取及指定账号 Tunnel 编辑的授权 |
| IP_CF_ZONE | CF 区域域名，例如 example.com |
| IP_EXTERNAL_ADDRESS | 平台分配的外部主机名或 IPv4 |
| IP_MAP_TCP、IP_MAP_UDP、IP_MAP_HOME | 三个原生入口的外部端口；0 跳过 |
| IP_MAP_PANEL | 面板外部端口；0 不配置 DNS 备用 |
| IP_AIR_API_TOKEN | NVIDIA API 接受的 Bearer 令牌，不是未经交换的任意 NGC Key |
| IP_AIR_INTERFACE_ID | 明确的 NVIDIA 外部接口 UUID |
| IP_HUB_URL | 自己的汇总入口；默认无 |
| IP_PRIVATE_RESOURCES | 设置汇总地址时默认 1：后台代建；0：使用部署者自己的 CF 资源，再登记后台 |
| IP_HUB_ENROLLMENT | 管理员签发、15 分钟有效的一次性接入码 |
| IP_HY2_CERT | 独立验证器用于核验自签 HY2 的证书文件 |

首台新机器不可能无授权读取另一台电脑的密钥。全自动的含义是有必要授权后无需逐项选择；授权缺失不能用公开密钥或默认私人账号补齐。重复运行会从 root 私有状态复用，不再要求重复提供。

CF API 令牌保存在 cloudflare.json，运行隧道令牌保存在 tunnel.token，通过 --token-file 使用，避免放到进程参数。下载 cloudflared 时检查官方 GitHub 发布的 SHA256 并记录版本。sing-box 固定 1.14.1，下载也核验官方摘要；无法校验不执行下载文件，可以复用系统现有程序。

## 7. 验证与输出

verify-node.py 为每个候选启动临时 sing-box，只监听 loopback。经候选节点连续两次访问 HTTPS IP 回显服务，记录成功、出口 IP 和耗时，关闭并清理临时进程。curl 强制不使用 NO_PROXY 绕过代理。

公开 WS 使用正常 CA 验证。自签 HY2 必须提供证书并与链接 pinSHA256 完全匹配，再把该证书交给测试客户端信任；不会在验证器里静默忽略认证。原分享 URI 的 pinSHA256 是否被具体 v2rayN 核心执行，仍需客户端版本验收。

退出码：0 表示三个入口及面板均通过 VPS 发起的公开自测；2 表示部分项目未通过。不是带宽评分，也不是用户所在网络的验收。客户端可另外执行 verify-node.py --links 文件 --binary 核心路径 --scope client-test；不要上传含凭据的链接文件。

## 8. 私人模式不向 VPS 下发 CF 管理令牌

设置自己的 IP_HUB_URL 和一次性授权时，默认通过后台代建资源；VPS 只获得自己的机器令牌和隧道运行令牌。后台持有 CF 管理 Secret。DNS 证书 hook 也通过后台限定的本机 TXT 接口完成。

不设置汇总地址的普通使用者，必须自己提供 CF 授权/域名；不会自动使用维护者资源。IP_PRIVATE_RESOURCES=0 可明确选择自有 CF 资源模式。上文 cloudflare.json 与 dns-credentials.ini 是自有 CF 模式的路径，默认私人代建不创建这些全局授权文件。

如显式要求接入汇总后台而登记失败，整体返回 2，即使原生节点已经可用；错误与有效链接分开保留。
