# 杭州 ECS 预生产部署手册

> 适用环境：阿里云华东 1（杭州）免费试用 ECS，Ubuntu 24.04、4 核 8 GB。
>
> 定位：工程验收、浏览器联调、容量测试和故障演练。该实例不满足中国内地 ICP 备案条件，不作为长期公开站点。

## 1. 访问边界

预生产环境通过公网 IPv4 和 HTTP 访问，但必须在阿里云安全组中限制来源：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `80/tcp` | 自己电脑的公网 IP；压测时临时增加压测机公网 IP | Web、API 和 SSE 验收 |
| `22/tcp` | 自己电脑的公网 IP；仅在使用 SSH 时开放 | 运维连接 |

不要开放 `443`、`3000`、`8000`、`5432` 和 `6379`。其中 Web、API、PostgreSQL 和 Redis 只在主机回环地址或 Compose 网络内通信。

HTTP 模式仅用于受限预生产环境。正式公网环境继续使用 `docker-compose.prod.yml`、域名、HTTPS 和 Secure Cookie，不能把本配置直接当作生产配置。

## 2. 拉取代码

私有仓库优先使用只读 Deploy Key；也可以在服务器使用 GitHub CLI 的设备授权登录。不要把个人访问令牌写入命令历史、仓库或环境文件。

```bash
git clone https://github.com/hkqxhy/agentic-rag.git
cd agentic-rag
git rev-parse --short HEAD
```

部署前记录提交号，后续压测报告、故障演练和回滚记录都引用这个提交号。

## 3. 创建预生产配置

```bash
cp deploy/env/staging.env.example deploy/env/staging.env
chmod 600 deploy/env/staging.env
openssl rand -hex 32
openssl rand -hex 32
```

编辑 `deploy/env/staging.env`：

- `STAGING_ORIGIN` 改为 `http://<ECS 公网 IPv4>`，末尾不要加 `/`；
- 将两次 `openssl` 输出分别填入 `POSTGRES_PASSWORD` 和 `AGENTIC_RAG_AUDIT_HASH_KEY`；
- 不要修改为真实生产账号或生产密钥；
- 初次验收保留默认限流参数。

真实的 `staging.env` 已被 Git 忽略，不得提交。

杭州 ECS 访问 Docker Hub 不稳定，预生产模板把 PostgreSQL、Redis、Caddy、Python 和 Node.js 基础镜像显式指向 Amazon ECR Public。Compose 与 Dockerfile 都保留可配置镜像参数，本地开发和具备正常 Docker Hub 网络的环境仍使用原始官方镜像名。

Python 依赖安装同样通过构建参数区分环境：杭州预生产默认使用阿里云 PyPI HTTPS 镜像，其他环境默认使用官方 PyPI。Dockerfile 不再在每次构建中升级 pip，减少一次不必要的跨境下载和不可重复变化。

## 4. 启动

```bash
chmod +x deploy/ecs/deploy-staging.sh
./deploy/ecs/deploy-staging.sh
```

脚本会拒绝示例 IP 和占位密钥，校验 Compose 合并结果，构建镜像，执行 Alembic 迁移并启动 PostgreSQL、Redis、API、Worker、Web 和 Caddy；服务健康后自动校验并发布 `knowledge/official`，最后打印 active 文档、Chunk 和 Embedding 数量。发布门禁失败时脚本返回非零状态，不会用 fixture 或空语料覆盖正式索引。

查看状态：

```bash
docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  ps

curl -fsS http://127.0.0.1/health/live
curl -fsS http://127.0.0.1/health/ready
```

在已加入安全组白名单的电脑浏览器中访问 `http://<ECS 公网 IPv4>`。


确认知识配置和发布结果：

~~~bash
docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T worker sh -lc 'printf "%s\n" "$AGENTIC_RAG_SOURCE_PATHS"'

docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T postgres psql -U agentic_rag -d agentic_rag \
  -c "SELECT status, authority_level, COUNT(*) FROM knowledge_documents GROUP BY status, authority_level ORDER BY status, authority_level;"
~~~

结果中线上来源应为 `knowledge/official`，当前发布项应为 `active/official`；`test_only/fixture` 可以保留为历史归档，但不能成为 active 检索来源。

## 5. 浏览器验收

至少验证以下路径，并只使用测试账号：

1. 注册、退出和重新登录；
2. 创建、重命名、搜索和删除对话；
3. 发送消息并观察 SSE 流式增量；
4. 刷新页面后恢复对话和消息；
5. 中止生成并确认状态可恢复；
6. 使用第二个账号确认无法访问第一个账号的资源；
7. 验证“校园卡弄丢了怎么办”“统一身份认证密码忘了怎么办”能返回正式来源与引用；歧义、安全攻击和领域外问题不进入检索。

## 6. 远端压测与证据

k6 必须从本地电脑、CI Runner 或另一台机器运行，不能与被测服务争抢同一台 ECS 的 CPU 和内存。

```bash
k6 run \
  -e BASE_URL="http://<ECS 公网 IPv4>" \
  -e VUS=10 \
  -e DURATION=2m \
  load/k6/phase1-smoke.js
```

先执行 10 VU smoke，再执行 20 VU。压测期间在 ECS 记录：

```bash
docker stats --no-stream
free -h
df -h /
```

保存 k6 摘要、提交号、测试时间、Compose 状态和资源快照。完成 Worker 中断恢复、数据库备份恢复和 soak 测试后，再形成 Phase 1 容量结论。

## 7. 一键韧性验收

完成基础功能与压测后，可以运行一键韧性验收。脚本会短暂停止 Worker、验证积压任务恢复，重启 API/Web 验证持久化，并将 PostgreSQL 备份恢复到临时隔离数据库后立即清理；不会删除正式数据卷：

```bash
sudo bash ./deploy/ecs/verify-staging-resilience.sh
```

执行期间测试环境会有两次短暂服务扰动，不应在他人正在演示或使用时运行。脚本会保留一个随机测试账号用于审计，但会软删除其测试对话。

## 8. 停止与重新部署

停止应用但保留数据库和 Redis 数据：

```bash
docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  down
```

不要使用 `down --volumes`。重新开机后拉取已通过 CI 的提交，再运行 `deploy-staging.sh`。

完成测试后，在阿里云安全组中删除临时的 80 端口来源规则，避免遗留公开入口。
