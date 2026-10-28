# InstaBox zhongyi — GitHub Actions Watchdog

针对现有机器 `zhongyi` 的独立监控目录，不改动本仓库其他程序。

- 每 15 分钟（UTC 分钟 07/22/37/52）执行一次 GitHub Actions；也可手动触发。
- 机器正在运行：通过 InstaBox activity lease 尝试续约 3600 秒。
- 机器自动暂停：调用官方 `resume` API。
- 机器已停止：调用官方 `start` API（冷启动；**原有后台进程不会自动恢复**）。
- 不删除机器、不创建新机器、不改远端 SSH 配置。
- 若 lease API 不可用，回退为一次短 `exec`，但不保证阻止空闲暂停。

## 必须配置（一次）

打开仓库 **Settings → Secrets and variables → Actions → New repository secret**，
名称填：`INSTABOX_API_KEY`，值填你的 InstaBox API Key。**不要把密钥写进仓库源码**。

进入 **Actions → InstaBox zhongyi Watchdog → Run workflow** 手动执行一次，看日志是否有 `WATCHDOG_OK`；此后定时运行。
需要停止监控时在 Actions 中禁用该 workflow，或删除这个工作流文件。手动暂停/停止机器时，工作流会在下次定时任务尝试恢复它。

## 重要限制

InstaBox 控制台显示了 **Auto-pause: 30 minutes** 和 **Max runtime: 8 hours**。活动租约只针对空闲暂停，不等于取消平台强制的最长运行时间。若机器在 8 小时处被平台关闭，最多只能尝试通过公开生命周期 API 再次启动，可能出现服务中断、限额拒绝或重启丢失内存/进程状态。GitHub Actions 定时执行并非准点保证。

持续在线、零中断的 SSH/VPS 服务需要平台正式提供的 always-on 方案或常驻 VPS。后台服务需要 systemd 等开机自启安排，持久化数据应放在 InstaBox 的 `/data`。
