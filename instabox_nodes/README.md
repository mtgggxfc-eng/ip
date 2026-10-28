# IP 项目 — 私有订阅输出模块

该目录只增加独立的订阅服务，不修改原来的六条节点生成、sing-box、家宽代理或 Cloudflare 管理面板。

目标：给 v2rayN 提供**一个 HTTPS 订阅 URL**，其中包含 TCP、HY2 UDP 和家宽出口各一条。未通过真实外部验收时，服务器返回 HTTP 503，绝不把候选链接当可用节点。

## 订阅数据来源

使用服务器上的**私有** JSON，绝不上传到这个公开 GitHub 仓库。格式：

```json
{
  "tcp": {"uri": "vless://<verified-connection-parameters>", "verified": true},
  "udp": {"uri": "hysteria2+realm://<verified-realm-and-credentials>", "verified": true},
  "home": {"uri": "vless://<verified-residential-exit>", "verified": true}
}
```

必须分别有真实的外部握手、有效代理出口和家宽出口证据，才能将对应 `verified` 设为 `true`。住宅/家宽出口还需要核实实际出口地址，不能把美国数据中心 IP 当成家宽。

启动时通过环境变量传入 `IP_SUBSCRIPTION_TOKEN`（至少 32 个随机字符）和 `IP_PRIVATE_NODES_FILE`（私有 JSON 路径）；可选 `IP_SUBSCRIPTION_BIND`、`IP_SUBSCRIPTION_PORT`。访问 `/s/<token>` 返回标准 Base64 编码的三条 URI，v2rayN 可按订阅导入。建议仅监听回环，由 HTTPS 反向代理发布；不要开放 HTTP 明文公网入口。

**重要限制：** GitHub Pages/公开仓库不能安全托管包含账户密码的可读订阅；订阅服务应部署到持续在线且有 HTTPS 的基础设施。InstaBox 免费机器 8 小时强制暂停/冷却期不能被 GitHub Actions 续租消除。单靠两台免费 InstaBox 也不能承诺真正 7×24 无中断。公共 Hysteria Realm 牵线不保证 Windows 端打洞成功，且 v2rayN 客户端对 Realm URI 的兼容性必须实测。

当前验收：InstaBox B HTTPS 临时端口曾从 A 获得 HTTP 200，随后测试端口已取消发布；A↔B HY2 Realm/代理出口通过；Windows→B Realm 打洞超时；正式 TCP WS、家宽出口、完整订阅均未验收。请不要标为已交付。
