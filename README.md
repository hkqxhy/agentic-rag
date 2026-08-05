# Agentic RAG

面向南京大学新生事务的、强调证据边界与可追溯引用的智能助手。仓库正在从可运行的本地 RAG 基线迁移为可部署、可评测、可扩展的 Agentic RAG 工程。

当前已进入 Phase 1。新的工程主干已经具备持久会话、异步任务、Redis 事件流、SSE、ChatGPT 式 Web 界面、数据库迁移、Docker Compose 和分层 CI。Agent 决策与新版知识检索尚未接入；现阶段 Worker 返回的内容只用于验证工程链路，不会冒充真实校务答案。

## 一键启动 Phase 1

需要 Docker 和 Docker Compose。首次启动会构建镜像、初始化 PostgreSQL，并启动 Redis、API、Worker 与 Web：

```bash
docker compose -f deploy/compose/docker-compose.yml up --build
```

启动完成后访问：

- Web：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- 存活探针：<http://localhost:8000/health/live>
- 就绪探针：<http://localhost:8000/health/ready>

停止服务：

```bash
docker compose -f deploy/compose/docker-compose.yml down
```

数据保存在 Compose 命名卷中。只有明确要清空本地数据库时才使用 `down --volumes`。

## Phase 1 已实现

- FastAPI 异步 API、分层配置、请求 ID、Server-Timing 和 OpenTelemetry 接入点；
- PostgreSQL 会话、消息和 Agent run 模型，以及 Alembic 迁移；
- Redis 任务队列、可重放事件流、心跳、游标、取消信号和事件过期；
- 独立 Worker 与流式响应链路，API 副本不持有会话内存；
- Next.js App Router 前端：新建、搜索、切换、重命名和删除对话，底部输入框，SSE 流式响应，停止生成，浅色/深色模式与移动端侧栏；
- 后端单元测试、PostgreSQL/Redis 集成测试、前端 lint/typecheck/build 和 Compose 校验；
- 保留 V1 检索基线，后续可用于新版 RAG 的回归与消融对比。

完整实现边界、API 契约与未完成门禁见 [Phase 1 实施记录](docs/PHASE1_IMPLEMENTATION.md)。总体路线见 [工程化重做方案](docs/REBUILD_PLAN_V1.md)。

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
src/agentic_rag/             Phase 1 API、数据层、队列与 Worker
src/agentic_rag_v1/          可运行的 V1 检索基线
migrations/                  Alembic 数据库迁移
deploy/                      Dockerfile 与 Compose
tests/unit/                  Phase 1 单元测试
tests/integration/           PostgreSQL + Redis 纵向链路测试
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
