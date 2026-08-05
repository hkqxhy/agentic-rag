# Phase 1 验收报告

## 结论

Phase 1 已在杭州地域的阿里云 ECS（4 核、8 GB、Ubuntu 24.04）完成部署与验收，可以作为 Phase 2 的稳定工程底座。验收覆盖功能闭环、故障恢复、数据恢复和 10/20 并发档压力测试，没有发现 HTTP 请求失败或数据丢失。

## 验收基线

- 验收提交：`4173627`
- 部署组件：Caddy、Next.js、FastAPI、Worker、PostgreSQL、Redis
- 服务入口：Caddy 80 端口同源代理
- 容量假设：约 100 名注册用户，10 至 20 名峰值在线用户

## 自动化结果

| 项目 | 结果 |
| --- | --- |
| 远程功能 E2E | 2/2 通过；消息受理约 188 ms，SSE 首事件约 101 ms，完整链路约 1.69 s |
| 10 VU / 2 min | 410 次 HTTP，0% 失败，181 次完整问答，E2E p95 约 795 ms |
| 20 VU / 2 min | 843 次 HTTP，0% 失败，355 次完整问答，E2E p95 约 2.18 s |
| Worker 中断恢复 | 排队任务在 Worker 恢复后完成 |
| 服务重启持久性 | API/Web 重启后账号、会话和消息仍可读取 |
| PostgreSQL 恢复 | 备份成功恢复到隔离的临时数据库 |

最终恢复脚本输出：`PASS: Worker recovery, service restart persistence, and isolated backup restore`。

## 资源快照

空闲时 API、Worker、Web、Caddy、PostgreSQL、Redis 合计占用远低于 1 GB；主机可用内存约 6.0 GiB，Swap 几乎未使用，系统盘剩余约 29 GiB。当前 4 核 8 GB ECS 对既定的演示和测试容量有充足余量。

## 边界

上述时延来自 Phase 1 的工程链路回复，不等同于云端大模型生成时延。Phase 2 接入千问后，需要分别统计检索耗时、首 token、生成耗时和端到端时延，并重新执行 10/20 并发测试。

