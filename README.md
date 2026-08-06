# Agentic RAG

面向南京大学新生事务的、强调证据边界与可追溯引用的智能助手。仓库正在从可运行的本地 RAG 基线迁移为可部署、可评测、可扩展的 Agentic RAG 工程。

Phase 1 已完成 ECS 验收，当前进入 Phase 2.1。工程主干已将 LangGraph 有界状态图、查询路由、Advanced/Graph RAG、证据核验、引用元数据、千问兼容接口和无模型降级接入异步 Worker；账号、持久会话、Redis 事件流、SSE、ChatGPT 式 Web 界面、数据库迁移、Docker Compose 与分层 CI 保持不变。

## 一键启动

需要 Docker 和 Docker Compose。首次启动会构建镜像、初始化 PostgreSQL，并启动 Redis、API、Worker 与 Web：

```bash
docker compose -f deploy/compose/docker-compose.yml up --build
```

启动完成后访问：

- Web：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- 存活探针：<http://localhost:8000/health/live>
- 就绪探针：<http://localhost:8000/health/ready>

首次打开 Web 时创建普通账户。登录会话由 HttpOnly Cookie 保存，历史对话只对所属账户可见。

停止服务：

```bash
docker compose -f deploy/compose/docker-compose.yml down
```

数据保存在 Compose 命名卷中。只有明确要清空本地数据库时才使用 `down --volumes`。

## Phase 1 已实现

- FastAPI 异步 API、分层配置、请求 ID、Server-Timing 和 OpenTelemetry 接入点；
- PostgreSQL 普通账户、哈希会话、审计事件、对话、消息和 Agent run 模型，以及 Alembic 迁移；
- Argon2 密码哈希、HttpOnly/SameSite Cookie、资源所有权隔离和登录/提问限流；
- Redis 任务队列、原子限流、可重放事件流、心跳、游标、取消信号和事件过期；
- 独立 Worker 与流式响应链路，API 副本不持有会话内存；
- Next.js App Router 前端：注册/登录、登录态恢复、新建、搜索、切换、重命名和删除对话，底部输入框，SSE 流式响应、刷新续接、停止生成、浅色/深色模式与移动端侧栏；
- Caddy 同源反向代理、自动 TLS、生产环境模板和 ECS 部署脚本；
- 后端单元测试、PostgreSQL/Redis 集成测试、前端 lint/typecheck/build 和 Compose 校验；
- 保留 V1 检索基线，后续可用于新版 RAG 的回归与消融对比。

完整实现边界、API 契约与未完成门禁见 [Phase 1 实施记录](docs/PHASE1_IMPLEMENTATION.md)。总体路线见 [工程化重做方案](docs/REBUILD_PLAN_V1.md)。

## Phase 2.1 已接入

- LangGraph 状态图：标准化、意图/复杂度路由、直接回答、澄清、RAG 和证据核验；
- 复用 Advanced RAG 与 Graph RAG 基线，支持查询改写、混合召回、融合重排、纠错检索和质量诊断；
- Worker 复用单个 Agent runtime，把节点轨迹、置信度、来源、警告和检索诊断持久化到消息元数据；
- 前端在回答下方展示最多三个知识来源，刷新历史对话后仍可恢复；
- 兼容阿里云百炼 OpenAI 接口，未配置 API Key 时自动使用可审计的抽取式回答，不阻塞工程验收。

Phase 1 的实测证据见 [Phase 1 验收报告](docs/PHASE1_ACCEPTANCE_REPORT.md)。Phase 2 的真实千问 2/5 并发结果、20 用户目标门禁、100 用户突发上限和对外表述边界见 [Phase 2 容量与效果基线](docs/PHASE2_CAPACITY_BASELINE.md)。

准备项目面试时，可按 [项目实现细节与面试追问手册](docs/PROJECT_INTERVIEW_GUIDE.md) 阅读完整请求链路、Agent/RAG算法、数据一致性、安全、部署、测试、局限和参考回答。

ECS 公网部署的域名、Secret、安全组和启动步骤见 [ECS 部署手册](docs/ECS_DEPLOYMENT.md)。

## 本地开发

后端要求 Python 3.12 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
alembic upgrade head
agentic-rag-api
```

另开终端启动 Worker：

```powershell
.\.venv\Scripts\Activate.ps1
agentic-rag-worker
```

前端要求 Node.js 22 和 pnpm 11：

```powershell
pnpm install
pnpm --dir apps/web dev
```

本地开发仍需可访问的 PostgreSQL 与 Redis；最省事的方式是只用 Compose 启动这两个依赖。

## V1 基线

V1 保留了 BM25 + 字符 n-gram 混合召回、RRF、轻量重排、证据质量判断、可选 Graph 增强、来源编号和无模型降级。

```bash
python -m agentic_rag_v1.cli --json "这是真实的学校通知吗？"
python agentic_rag_v1_server.py --host 127.0.0.1 --port 8002
python -X utf8 -m agentic_rag_v1.evaluate --suite smoke --output-dir reports
```

云端千问配置见 [QWEN_API_SETUP.md](docs/QWEN_API_SETUP.md)。真实密钥只能放在 `.env.local` 或部署平台 Secret 中。

## 仓库结构

```text
apps/web/                    Next.js 会话产品
src/agentic_rag/             API、数据层、队列、Worker 与 LangGraph Agent
src/agentic_rag_v1/          可运行的 V1 检索基线
migrations/                  Alembic 数据库迁移
deploy/                      Dockerfile 与 Compose
tests/unit/                  工程与 Agent 单元测试
tests/integration/           PostgreSQL + Redis 纵向链路测试
load/k6/                     ECS smoke/soak 压测脚本
tests/test_agentic_rag_v1.py V1 回归测试
knowledge/                   schema、manifest 规则和脱敏 fixture
eval/cases/                  版本化评测样例
docs/                        架构、部署、评测与交付文档
legacy/prototype/            不再受支持的早期实验代码
```

## 知识库边界

源码仓库只保存知识元数据契约、非敏感 manifest 和脱敏 fixture。原始 PDF、聊天记录、解析产物、向量索引与评测报告不进入 Git。首版采用人工维护资料，并在 Phase 2 实现管理员上传、校验、预览、发布、回滚和增量索引接口。

## 验证

```bash
ruff check src/agentic_rag tests/unit tests/integration
pytest -q tests/unit tests/test_agentic_rag_v1.py
python -m compileall -q src tests agentic_rag_v1_server.py
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
docker compose -f deploy/compose/docker-compose.yml config --quiet
```

集成测试需要 PostgreSQL 与 Redis，并显式设置 `AGENTIC_RAG_RUN_INTEGRATION=1`。CI 会自动创建两个服务、执行迁移并验证数据库、队列、Worker 和事件流的完整链路。

## 已确认的首版约束

- 面向约 100 名注册用户设计，目标峰值 10 至 20 人在线；
- 阿里云 ECS 4 核 8 GB 用于开发、集成、压测和秋招演示；
- 使用阿里云百炼千问 API，不在 CPU ECS 上部署 vLLM；
- 使用普通账号，暂不接学校统一身份认证；
- 学校资料先人工维护，保留受控更新接口，不开发自动爬虫。

项目对外只陈述已经实现和实测的数据。在没有真实用户运营记录前，应表述为“按约 100 用户规模设计并压测验证”。
