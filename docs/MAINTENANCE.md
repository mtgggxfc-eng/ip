# 维护手册

## 当前版本边界

最终结果固定为三个装饰框：节点链接、面板信息、操作提醒。检测说明与日志位置必须位于第一框之前；第三框之后不再追加信息。端口标题使用【请在后台开放端口】，节点 URI 不添加边框前缀，以便整段复制导入。

终端已改为精简结果。映射未配置时复制区可以含需要改地址/端口的原始链接，不代表已得到公网入口。CF 正常时仍按用户要求显示可选备用端口。系统安装细节查看当次 /root/ip-install.*.log，完整检测说明查看 /etc/residential-stack/deployment-details.txt，均可能含凭据，不提交日志。

2026-10-19 修复已生成信息隐藏、面板端口漏报，以及私人授权 hub_request 请求头遗漏。复制区可以包含待放行入口；links.json 的就绪含义保持不变。具体测试见 TESTING.md。

2026-10-18 已发布试用版，汇总 Worker 已上线，旧 Worker 保留为 LEGACY 绑定。本轮没有在用户 VPS 安装新版。后续维护必须核对 GitHub 提交、线上 Worker 和 VPS 服务，不能只根据本地文件判断已部署。验证范围见 TESTING.md。

本地顶层 install-three-nodes-vps.sh 是 jkyj.sh 的兼容副本；发布使用 jkyj.sh。源码模板与所有模块都应进入同一版本，不再依赖开发者原来 work/ 内的临时构建程序或密钥路径。

## 构建

```bash
python scripts/build.py
python scripts/check.py
```

build.py 内嵌 panel-original.html 到 residential-panel.py，复制到 hub/panel.html，把 Python 模块和更新器装入 jkyj.sh，并生成卸载包装、备用长命令。Base64 只是打包，不是加密，不能把秘密藏进载荷。

每次改模板或模块都重建。不要只上传 Python 源码、忘记自包含入口仍带旧代码。生成结果应可重复；固定输入产生相同 SHA256。

开发测试依赖：Python 3.10+、Node 20+、Bash；TLS 测试额外使用 requirements-dev.txt 的 cryptography。生产模块使用 Python 标准库。协议实测显式提供 sing-box/Xray 程序路径，不能覆盖桌面客户端配置。

## 发布检查

1. 完成本地检查，记录具体结果。
2. 按 release-files.json 检查待发布文件，不包括真实云配置、节点链接、私钥、运行数据、临时日志或 .dev.vars。
3. 在授权的试验 VPS/试验 CF 域名做真实安装、重复运行、断线恢复、卸载测试；不要直接替换生产域名。
4. 从用户网络以实际 v2rayN 导入链接、验证出口。自测范围写清楚。
5. 发布后比较 GitHub 原始文件摘要与构建结果；更新 CHANGELOG 和测试记录。
6. 把“尚未发布”的状态改为真实版本、提交或发布日期。不能在测试未完成时删掉限制说明。

## 升级和回滚

保留 auth.json、settings.json、network.json、cloudflare.json、hub.json、原生/WS 配置、当前二进制和服务文件的私有备份；包含密钥，不上传仓库。

新版正常重跑保持实例编号、面板密码、UUID、WS 路径、隧道编号。没改代码和内部接口就不重启住宅代理，避免反复断开出口。注意：面板改内部端口会按设计重启自身代理和相关入口。

失败时看中文阶段与网络报告，不能通过删除整个目录来碰运气。需要回滚时先恢复上一版本文件、配置及本项目 unit，检查 sing-box 配置，再只重启本项目相关服务。不要重启/删除原有 x-ui 或其他 sing-box。

## 卸载

先 --dry-run。xiezai.py 检查明确的 unit Description/ExecStart 和原生配置标签；混入其他入口或路径经过符号链接时停止。

卸载顺序：停止自有隧道连接，清理已登记的自有云资源/汇总登记，再停止自有 timer、节点、WS、代理、面板，清理自己的目录。远程资源清理失败会保留凭据及状态，以便再次运行；可能已有部分云资源被清理，账本会逐项更新。

--keep-cloudflare 保留云资源并把 network.json、cloudflare.json、air.json、hub.json 和清理模块复制到 /root/ip-cloudflare-cleanup。后续可执行：

```bash
sudo python3 /root/ip-cloudflare-cleanup/network-adapter.py cleanup-cloudflare --state-dir /root/ip-cloudflare-cleanup
```

只有 cloud API 返回的资源 ID、内容、名称和接口均匹配本地账本，才删除。已有 DNS、隧道、复用的 NVIDIA 服务不删除。原生 HY2 证书可能共享，保留。系统包和防火墙放行规则按旧约定保留；不要宣称卸载把系统完全恢复到安装前。

## 故障定位

| 现象 | 先检查 | 不应误判 |
|---|---|---|
| IP 证书失败 | 外部是否真是 80、Python/Certbot、acme-logs | 内部 80 映射到随机端口不满足验证 |
| 隧道不通 | DNS、TCP/UDP 7844 出站、residential-tunnel 日志、CF API 权限 | 不能把服务 active 当成公网连接成功 |
| WS 不通 | 隧道 ingress、独立子域名、边缘证书、路径、UUID/密码、核心配置 | 不要随意关闭 TLS 验证 |
| HY2 本机正常、外部失败 | 平台 UDP 映射/安全组、客户端版本 | Other 不代表 UDP |
| 住宅出口未就绪 | manager.log、ovpn_err_0.log、候选国家、TUN、OpenVPN 出站 | 本机 SOCKS 出站失败不是面板入站没放行 |
| 面板改端口失败 | 原生与 WS 检查结果、服务归属、回滚后的配置 | 不应留下两个入口端口不一致 |
| 后台选机器无数据 | 机器是否登记、该机 HTTPS、凭据、明确机器编号 | 不回退到其他机器的配置假装成功 |
| 接入码失败 | 是否过期/已用、签发的后台、URL | 不复用管理员密码作为机器凭据 |
| 区域没有可用出口 | VPNGate 候选及国家供应 | 不保证一定是目标国家或商业住宅 IP |

## 外部依赖与已知限制

- sing-box 固定 1.14.1；cloudflared 安装使用官方 latest 资产摘要并记录实际版本，没有后台自动替换系统程序。
- Certbot/dns-cloudflare 固定 5.4.0，Python 至少 3.10。
- 公开检测依赖 HTTPS IP 回显服务；服务故障会造成假阴性，未加入带宽压力测试。
- 仍以 IPv4 VPS 为主要支持目标，没有完整 IPv6-only 流程。
- 发现器识别活动 sing-box，x-ui-only 情况补建独立入口，不修改 x-ui 数据库。
- 防火墙适配不是所有云厂商的安全组 SDK；NVIDIA API 也需要明确接口编号。
- 无凭据的全新映射机器无法自动替所有者创建私人 CF 资源。
- 汇总后台已有“新增机器”生成私人安装命令的 UI；没有自动密钥轮换器和持续跨证书入口切换。

## 尚待真实验收

见 TESTING.md。下一位 AI 应保留已有备份，先在试验域名验证变更。旧 Worker 的 global_config 仍是兼容分组，旧机器并未逐台迁移；不要静默假装已按机器隔离。

## 发布清单与历史私人制品

只使用 scripts/release_files.py 返回的文件。开发制品目录曾包含真正的节点二维码、链接文本和旧安装命令，不能 git add 整个制品目录。release-files.json 仅允许维护源码、生成入口、文档和非敏感测试；不发布历史 PNG、运行 JSON、节点 TXT 或私有密钥。标准干净仓库没有这些私人制品。

私人资源模式的 CF 账号令牌只在 Worker Secret。不要把开发者电脑密钥文件中的全权令牌复制到每台新 VPS。维护者只需私下签发一次性码；机器只持单机运行授权。
