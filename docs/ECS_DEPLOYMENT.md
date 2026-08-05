# 阿里云 ECS 部署手册

## 部署时点

Phase 1 代码先在本地静态检查和 GitHub CI 中关闭账号、资源隔离、幂等、限流、迁移、前端构建与 Compose 门禁，再部署到 ECS。这样服务器承担的是验收、压测与演示环境，而不是在线调试匿名接口。

当前目标机器为 Ubuntu 24.04、4 核 8 GB、40 GB 系统盘，面向约 100 名注册用户和 10–20 人峰值在线。千问通过阿里云百炼 API 调用，不在 ECS 上运行模型权重。

## 1. 部署前准备

1. 准备一个域名或子域名，将 A 记录指向 ECS 公网 IP；
2. 安全组只开放 `80/tcp`、`443/tcp`、`443/udp`，SSH 或远程管理端口仅允许自己的出口 IP；
3. 不开放 `3000`、`8000`、`5432` 和 `6379`，这些端口只在主机或 Compose 网络内部使用；
4. 确认 Docker Engine、Compose plugin、Git 与 curl 已安装；
5. 私有仓库使用 GitHub CLI 登录或只读 deploy key 拉取，不把个人访问令牌写入仓库或部署文件。

## 2. 拉取与配置

```bash
git clone https://github.com/hkqxhy/agentic-rag.git
cd agentic-rag
cp deploy/env/production.env.example deploy/env/production.env
chmod 600 deploy/env/production.env
```

编辑 `deploy/env/production.env`：

- `SITE_ADDRESS`：已经解析到 ECS 的域名；
- `POSTGRES_PASSWORD`：独立随机密码；
- `AGENTIC_RAG_AUDIT_HASH_KEY`：另一份独立随机密钥；
- 初版限流参数可以保持模板值。

可用以下命令分别生成两份 Secret：

```bash
openssl rand -hex 32
```

生产配置要求 HTTPS 安全 Cookie 和非默认审计密钥，缺少时 API 会拒绝启动。

## 3. 启动

```bash
chmod +x deploy/ecs/deploy.sh
./deploy/ecs/deploy.sh
```

脚本会先校验 Compose 合并结果，再构建镜像、执行一次性 Alembic 迁移并启动 PostgreSQL、Redis、API、Worker、Web 与 Caddy。Caddy 使用同一域名代理前端和 `/api`，并为 SSE 禁用响应缓冲。

## 4. 验收

```bash
curl -fsS "https://${SITE_ADDRESS}/health/live"
curl -fsS "https://${SITE_ADDRESS}/health/ready"
docker compose \
  --env-file deploy/env/production.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  ps
```

浏览器验收至少覆盖：注册、退出、重新登录、创建对话、流式回答、刷新恢复、停止生成、另一个账户无法访问原账户对话。

完成浏览器验收后，从另一台机器运行首轮压力测试：

```bash
k6 run \
  -e BASE_URL="https://${SITE_ADDRESS}" \
  -e VUS=10 \
  -e DURATION=2m \
  load/k6/phase1-smoke.js
```

脚本为每个虚拟用户注册独立普通账户，循环创建对话、提交带幂等键的问题，并轮询持久化的助手消息。默认门禁为请求失败率低于 1%、HTTP P95 小于 1.5 秒、端到端 P95 小于 8 秒。先用 10 VU smoke，再逐级提高到 20 VU；不要直接从高并发起步。

## 5. 回滚与数据

- 应用回滚：切换到上一个已验证 Git commit，重新运行部署脚本；
- 数据库迁移在单独的 `migrate` 服务中执行，破坏性 schema 修改必须提前准备兼容窗口；
- PostgreSQL、Redis 和 Caddy 状态保存在命名卷中，正常更新不要使用 `down --volumes`；
- 正式演示前增加 PostgreSQL 逻辑备份，并在另一目录验证恢复流程。

## 尚未执行的服务器验收

代码进入远端 CI 后，再执行 ECS 首次部署、双 API/双 Worker 副本验证、Worker 中断恢复和 k6 压测。只有保存这些结果后，项目材料才写“完成部署与压测”；在此之前只写“具备可部署配置”。
