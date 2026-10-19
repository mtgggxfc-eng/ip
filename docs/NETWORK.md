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
3. 都失败时，复制区仍保留已生成的原始入口；无映射时明确标注地址/端口待替换，不能把原始出口 IP 当成可用入站。验证通过清单仍只保存在 links.json。没有创建的协议不输出假链接。

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
| 两条路都缺条件 | 保留明确的待验证面板地址、本机 HTTPS 地址及内部端口；说明缺少的授权/映射，不标成可用 |

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

NVIDIA 是可选的平台适配器，不是通用流程的前提。配置 API Bearer 授权后，可按默认网卡 MAC 唯一匹配接口，也可显式指定 interface UUID；匹配不唯一就停止平台写入。每次重跑查询当前映射，复用已有同接口同内部端口的服务，不修改它；只记录和清理自己新建的服务。没有 UDP 字段，不自动创建所谓 UDP Other 服务。没有配置此适配器的授权时，不向它请求或询问该平台专属参数。

### 通用的首次部署与自动续装

第一遍自动部署能完成的组件，保留已生成配置，并提示缺少的外部条件。普通公网端口由用户在云平台放行后，第二遍按回车即可重新检测；本机握手失败不再直接中止后续信息输出。关键环境/配置错误仍会停止并提供日志。

随机 NAT 映射不能由内部端口推算出来。平台适配器有有效授权时自动读取；其他平台可一次性把映射结果写进自动生成的 /etc/residential-stack/port-mappings.json，之后每次重跑自动读取，不能假称无需任何映射来源就能发现任意厂商随机端口。

示例（真实内部端口由脚本自动填好，用户只补外部地址和外部端口；0 表示未知）：

```json
{"external_address":"worker.example.com","tcp":{"21088":17757,"47956":15083,"45062":18676},"udp":{}}
```

此文件是厂商无关的交接表，不含 API 凭据。映射变动后更新该文件，再自动重跑。有效平台 API 的最新查询覆盖文件中的旧值，显式环境变量最后覆盖。重新生成的链接和 /root/three-nodes-info.txt、three-nodes-links.txt 会更新，UUID、密码、内部监听端口保留；已导入客户端的静态 URI 需重新导入，不会凭空同步到客户端。

未知面板端口显示 `https://具体地址:____/  【端口待检测】`。已知平台外部主机名时直接使用它；只有出口 IP 时作为候选填入，并注明入站地址仍需确认。不能输出字面量“外部地址”或把未知端口默认为 443。

接口依据：[按 MAC 列出网卡](https://docs.nvidia.com/dsx-air/api-reference/interfaces/api-v-3-simulations-nodes-interfaces-list/)、[读取已分配的外部主机与端口](https://docs.nvidia.com/dsx-air/api-reference/services/api-v-3-simulations-nodes-interfaces-services-list/)。

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
| IP_AIR_INTERFACE_ID | 可选 NVIDIA 外部接口 UUID；不填则在有授权时按本机 MAC 唯一匹配 |
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

2026-10-19 起：/root/three-nodes-links.txt 是已生成链接的复制区，包括公网检测失败、等待端口开放的入口；/etc/residential-stack/links.json 继续只保存已通过验证的入口。network-report.json 的 generated_roles 与 ready_roles 分别记录生成和就绪，pending_panel_urls 记录未通过验证的面板候选。

面板 TCP 内部端口始终参加系统放行和终端提示，不再要求先有云端映射才显示。住宅内部 SOCKS/API/WS 端口不建议暴露公网。映射未知时终端显示面板映射模板，必须取得真实映射才能组成外部链接；公网出口 IP 不能替代映射地址。

### 简洁输出与原始链接

终端与 /root/three-nodes-info.txt 使用 concise_result()，只显示生成数/状态、链接复制区、面板地址或模板、登录信息及下一步。节点原始链接通过 generated_exports() 的 native_links 回退输出；即使映射未知也保留配置，必须明确提示替换地址/端口，不能升级 ready_roles 或写入已验证 links.json。

需要手动开放/映射的端口只列一次；已工作的 CF 链路不显示重复的必需入站步骤，但按用户要求保留一行“备用直连端口（可选）”。完整说明保存 /etc/residential-stack/deployment-details.txt；安装器另创建本次 /root/ip-install.XXXXXX.log，使用 umask 077，通过 IP_INSTALL_LOG 传给子进程。

quiet() 只捕获不需要用户交互的命令输出，保留退出码。不要全局重定向：初始菜单及手动参数提示必须可见。apt、下载进度、systemd 启用记录、更新器和本机握手细节写入日志；错误仍显示失败阶段与日志路径。安装日志在卸载后保留，便于排障；需由 root 自行清理。不使用尾部重复总结覆盖已显示的下一步。

私人授权请求必须保留明确 User-Agent。CF 403/1010 或 HTML 拦截页表示边缘拦截，不能直接判定登记码过期；后台 JSON 403 才提示码无效/过期/已用或机器授权失效，不输出原始响应中的任意内容。

## 8. 私人模式不向 VPS 下发 CF 管理令牌

设置自己的 IP_HUB_URL 和一次性授权时，默认通过后台代建资源；VPS 只获得自己的机器令牌和隧道运行令牌。后台持有 CF 管理 Secret。DNS 证书 hook 也通过后台限定的本机 TXT 接口完成。

不设置汇总地址的普通使用者，必须自己提供 CF 授权/域名；不会自动使用维护者资源。IP_PRIVATE_RESOURCES=0 可明确选择自有 CF 资源模式。上文 cloudflare.json 与 dns-credentials.ini 是自有 CF 模式的路径，默认私人代建不创建这些全局授权文件。

如显式要求接入汇总后台而登记失败，整体返回 2，即使原生节点已经可用；错误与有效链接分开保留。
