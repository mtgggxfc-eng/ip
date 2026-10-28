# InstaBox zhongyi 智能保活（GitHub Actions）

本目录独立管理你的 InstaBox 机器 zhongyi，不修改其他功能，机器 ID 固定为 `m_agqyeponmofni7xqt6yr574yja`。

## 工作原理

每 20 分钟查询一次官方 API 的真实机器状态，**不再每次都续租**：

1. 读取机器 `state`、`runStartedAt`、`maxRunMinutes`、`autoSuspendAfterSeconds`、`cooldownUntil`。
2. 从 GitHub Actions Cache 恢复上次 `PUT activity-leases` 成功后返回的精确 `expiresAt`。
3. 一次租约最长 3600 秒；**租约到期后 InstaBox 才开始新的空闲倒计时**。脚本据此估算自动暂停剩余时间。若高于 25 分钟安全窗口，则 **只观察、不续租**；临近窗口再续租，更新缓存。
4. 机器被暂停时尝试 `resume`；停止时尝试 `start`（这会冷启动）。
5. 当 `runStartedAt + maxRunMinutes` 即将触及 8 小时上限，记录强制暂停剩余时间，但**不会伪称续租可以突破该平台上限**。
6. 如果 API 给出 `cooldownUntil`，冷却期内不尝试无效唤醒；下次调度再检查。

预计每约 60–80 分钟续一次租约（取决于 GitHub 调度延迟、实际运行活动以及缓存），比原来每 15 分钟续一次明显减少写入。每 20 分钟的读取本身不会创建活动租约。

## 必须配置一次

在 GitHub 仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：
- Name: `INSTABOX_API_KEY`
- Secret: 你的 InstaBox API 密钥

然后进入 **Actions → InstaBox zhongyi Smart Watchdog → Run workflow** 手动执行一次。成功日志应包含 `WATCHDOG_OK` 和首次 `LEASE_RENEWED`；后续通常是 `NOT_DUE`。

不要把密钥写进源码或提交到 GitHub。取消自动保活可在 Actions 中禁用这个工作流。

## 为什么不只在关机前 1 分钟执行？

GitHub Actions Cron 最短 5 分钟，启动时间可能延误或漏触发；InstaBox 的机器 API 提供 `runStartedAt` 和最大运行分钟数，但**不公开实时的最后空闲活动时间**，同时没有 GET 租约接口。

因此无法保证「最后 60 秒／几十秒精确续租」。采用 20 分钟巡检和 25 分钟保护窗口，避免因调度误差导致机器意外自动暂停；从最近一次成功租约时间估算的自动暂停时间是保守值，不是平台对外承诺的精确休眠时刻。

## 8 小时 Max runtime 的限制

InstaBox 对免费机器的 `maxRunMinutes` 默认 480（8 小时），不是一个能用活动租约续期的倒计时。到达上限会**强制暂停**，进入平台冷却期（默认 10 分钟），并且在 `cooldownUntil` 到期前拒绝所有恢复调用。工作流会在后续轮次尝试恢复，因此不能实现真正 7×24 零中断。

Github Cache 会过期/清理：缓存找不到时脚本会直接续租一次并重建状态，是安全回退。GitHub Actions 调度也无法保证绝对准点或永远不中断。在公共仓库，GitHub 可能因 60 天无活动而自动禁用定时工作流。
