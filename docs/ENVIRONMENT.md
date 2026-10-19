# 环境自动检测、补全与证书兼容

## 实际问题与修复目标

Ubuntu 20.04 默认 Python 3.8.10，Certbot 5.4 要求 Python 3.10 或更新。旧脚本直接跳过 Certbot，却把任何 HTTPS 检测失败都提示为开放 80，导致用户已经放行仍看见不安全提示。

现在修复的是公共工具：自包含安装器内嵌 preflight.sh 和 cert-runtime.py，已有完整节点的续装分支也执行环境检查。不是针对某个 IP 的特殊分支，不在公开代码保存机器密码。

## 环境检查顺序

1. 原有系统预检检查 apt、systemd 与 x86_64/aarch64，缺 Python 时先安装发行版 Python。
2. 菜单后、节点发现前执行 src/preflight.sh。只补缺失 curl、openssl、ip/ss、iptables、OpenVPN、cron、flock 与 CA 组件；不做全系统 apt upgrade。
3. 缺 TUN 时尝试加载 tun 模块、创建缺失设备；检查能否打开设备以及 NET_ADMIN。平台不提供的容器权限无法由脚本强制获得，明确记录限制。
4. 检测 /opt 空间、关键依赖域名 DNS、时钟年份及 NTP 状态；不替换 /etc/resolv.conf、不添加第三方系统源、不更改系统 Python、不改内核调优参数、不自动重启机器。
5. 在网络目录归属校验完成后，把报告保存到 /etc/residential-stack/environment-report.json，权限 600。不能覆盖未知项目目录。
6. 原有节点发现/配置校验/服务恢复逻辑补建缺失组件，保留已有身份、端口、国家与账号。家宽上游可用性仍由住宅代理及协议测试检查，不用端口放行掩盖上游失败。

## 证书运行环境

- 先复用本项目可运行的 Certbot 5.4+。需要补建时，依次寻找可用的系统 Python 3.10+。
- 没有兼容 Python、或者发行版 venv/ensurepip 不完整时，使用隔离的托管 Python 3.12；不改 /usr/bin/python3、PATH 或 shell 配置。
- cert-runtime.py 固定 uv 0.13.1 的官方 x86_64/aarch64 Linux GNU 归档及 SHA256，校验通过后仅提取 uv 普通文件，不执行任意归档安装器。uv 在项目目录安装托管 Python，不向全局 bin 安装链接。
- 下载来源为 astral-sh/uv 和 Astral python-build-standalone；Python 下载由 uv 的发行元数据校验。证书包固定 Certbot 5.4.0，DNS 插件同版本。
- uv、Python 和缓存放在 /etc/residential-stack/cert-runtime；虚拟环境位于 /opt/residential-certbot。已有非本项目证书目录不覆盖，异常符号链接拒绝。
- 已有本项目虚拟环境的 Python 不可用时，使用选中的独立 Python 重建该虚拟环境；节点配置、证书账户、证书文件和凭据都在其他路径，不清除。
- DNS 备用证书入口同样复用该模块，因此不会在旧系统再次使用 Python 3.8 安装新版 Certbot。

参考：[uv 托管 Python](https://docs.astral.sh/uv/concepts/python-versions/)、[安装 Python](https://docs.astral.sh/uv/guides/install-python/)、[uv 安装选项](https://docs.astral.sh/uv/reference/cli/#uv-python-install)。

## 失败记录与端口提示

certificate-status.json 包含 stage、ok、reason、updated_at。stage 是 runtime、issue 或 applied，分别对应环境补全、证书申请、正式证书加载。摘要显示实际原因；运行环境下载失败、端口被其他服务占用、CA 限流不再被写成“80 未开放”。不停止其他服务抢占 80。

IP 证书在申请和续期前尝试放行本机 TCP 80；定时任务与手动请求使用 cert-renew.lock 串行。云映射有授权时尝试云端适配，本机开放成功并不代表云端已开放。

只有未通过/无法确认的公开端口才提示后台处理。TCP 连接与住宅上游协议测试分开；UDP 必须做真实协议测试。检测全部通过时第三框只提示导入链接。Certbot 申请完不常驻 80，不能据此反推未开放。

## 卸载与回滚

新运行文件都位于本项目 stack 目录内，沿用已验证目录归属后的卸载；/opt/residential-certbot 沿用 pyvenv.cfg 归属检查。没有新增全局 Python、第三方 apt 源或驻留环境管理服务。共享下载 Worker 不由单机卸载删除。

回滚前备份 settings.json、证书脚本、证书状态和现有证书；还原 public_cert/public_key 及证书脚本后只重启住宅面板。不要删除系统 Python、重装所有节点或清空整个 /etc/sing-box。

## 验证与边界

2026-10-19：在现有 Ubuntu 20.04/Python 3.8.10 上，运行通用 preflight 与更新器，自动准备 Certbot 5.4，申请并加载 Let’s Encrypt 正式 IP 证书，外部客户端以默认信任链成功验证面板 TLS。系统 Python 版本保持不变，auth.json 与 three-nodes.json 的前后 SHA256 相同。测试没有改其他节点或桌面 v2rayN。

随后运行完整安装器的自动续装分支：生成环境报告，证书 applied；TCP、UDP、住宅 TCP 和面板 TCP 均记录为可连接/通过对应测试，终端不再列出任何开放端口提示。直连和 Hysteria2 通过，住宅完整代理检测仍失败，退出码保持 2，没有伪报全部完成；账号及节点配置 SHA256 仍相同。这是已有机器续装测试，不等于所有新镜像首次安装验收。

本地测试覆盖校验失败不执行、旧 Python 自动隔离、未知目录保留、CA 错误分类、已通端口隐藏、TCP 入站成功但住宅上游失败、未知映射不猜地址、先放行后验证。

这不等于任意发行版、CPU、容器权限、运营商防火墙均能强制修好。当前实际旧系统验证是 Ubuntu 20.04 x86_64；其他架构、网络受限镜像、域名证书在旧系统的真实签发仍需对应测试。不能获取的平台权限、TUN、随机映射地址或外网连接会明确提示，不伪造成功。
