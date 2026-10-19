# 短安装入口

## 使用

```bash
curl -fsSL https://ip.aizoudd.ggff.net | bash
```

旧命令较长，是因为写出了 GitHub 原始文件完整路径，以及 root/sudo 权限判断。短入口把下载与权限判断移进 bootstrap/install.sh，仍运行 main 分支的同一个 jkyj.sh。

新部署默认家宽国家 JP。已有 settings.json 的国家优先，不因重跑而改动。

## 调用流程

1. curl 通过 HTTPS 从独立 ip-install Worker 获取 bootstrap/install.sh。
2. 引导程序以 077 权限掩码创建临时文件，完整下载 GitHub main/jkyj.sh；失败立即退出，不执行残缺文件。
3. root 直接运行 Bash；普通用户通过 sudo 运行。sudo 可能询问本机密码，与旧命令相同。
4. 安装器显示回车自动 / 2 手动菜单，沿用安装、续装与日志逻辑。
5. 安装器退出后删除临时文件，保留安装器退出码；结果框后不额外打印信息。

短命令依赖这个 HTTPS 入口和 GitHub。入口不可达时使用 README 中原始 GitHub 命令，不增加不可控的第三方短链接服务。

## 权限隔离

该入口公开提供安装脚本，不提供汇总登录、接入码、隧道令牌或任何 CF API 权限。Worker 没有 Secrets、D1、Service Binding，和 ip-hub 分开部署。不通过 IP 白名单或公开固定密码隐式授权。

需要使用私人汇总资源时，仍应从汇总面板“新增机器”复制专属命令。公共短命令不会自动登记私人汇总、不会为陌生使用者创建维护者的隧道/节点域名。公开下载域名仅用于分发脚本。

## 维护与自部署

- Worker 只接受根路径 GET/HEAD，其他路径 404、其他方法 405；响应 text/plain、nosniff、no-store。
- bootstrap/worker.js 以文本模块导入 install.sh。修改引导程序后单独重新部署这个 Worker；仅修改 jkyj.sh 时发布 GitHub 即可，入口下一次下载会拿 main 最新版。
- wrangler.example.toml 配置了 .sh 文本模块，无真实域名路由、密钥、账号或数据库。自行部署时给独立 Worker 绑定自己未占用的域名；不得覆盖现有 DNS/Worker。
- 生产实例名称 ip-install，域名 ip.aizoudd.ggff.net。域名/Worker 属于公共下载基础设施，不由 VPS 卸载脚本删除。VPS 不安装额外常驻服务；临时文件由引导程序退出清理。
- 回滚入口：部署 GitHub 历史版本的 bootstrap 两个源文件；回滚安装器：按常规发布流程回滚 main 的安装源与生成脚本。两者版本分别核对。
- 默认国家只改 src/install.sh 的新建分支并重建。不能替换已有机器的 settings.country。

## 验证范围

默认本地检查覆盖 Bash 语法，模拟 root/普通用户、下载失败不执行、安装器退出码、临时文件清理；Node 检查脚本内容完全一致、HTTP 方法和路径隔离。云端上线后通过 HTTPS 核对响应字节与源码。测试不在用户 VPS 执行安装，也不授予私人资源权限。
