# Agentic RAG

[![CI](https://github.com/hkqxhy/agentic-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/hkqxhy/agentic-rag/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/Agent-LangGraph-1C3C3C)
![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=next.js)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)

面向南京大学新生事务的可信问答系统。项目把有界 Agent、混合检索、证据校验、异步任务链路和会话产品整合为一套可部署、可评测、可降级的 Agentic RAG 工程。

仓库包含完整的 Web、API、Worker、数据库迁移、知识入库、评测、负载测试和阿里云 ECS 部署脚本。源码只提供脱敏测试资料，真实学校文件和 API 密钥不会进入 Git。

## 项目亮点

| 方向 | 已实现能力 |
| --- | --- |
| Agent | 使用 LangGraph 构建 normalize、classify、direct、clarify、rag、verify 有界状态图，路由和节点轨迹可审计 |
| RAG | BM25、字符 n-gram、Dense Retrieval 和轻量 GraphRAG 多路召回，使用加权 RRF 融合并执行证据质量检查 |
| 向量检索 | PostgreSQL 17 + pgvector + HNSW，支持内容哈希增量入库，以及 off、shadow、hybrid 灰度切换 |
| 可靠链路 | API 持久化后返回 202，Redis 负责队列、限流、取消信号和可重放事件流，独立 Worker 执行 Agent |
| 可信回答 | 来源编号、权威度和时效性建模；证据不足时澄清或拒答；模型不可用时回退为抽取式回答 |
| 会话产品 | Next.js 实现普通账号、历史会话、搜索、重命名、删除、停止生成、断线续接、主题和移动端布局 |
| 工程交付 | Docker Compose、Caddy、Alembic、健康探针、OpenTelemetry 接入点、GitHub Actions 和 k6 分层验证 |

## 系统架构

~~~mermaid
flowchart LR
    U["浏览器"] --> C["Caddy 同源网关"]
    C --> W["Next.js Web"]
    C --> A["FastAPI API"]
    A --> P[("PostgreSQL + pgvector")]
    A --> R[("Redis Queue / Streams")]
    R --> K["Async Worker"]
    K --> G["LangGraph Agent"]
    G --> H["Lexical + Dense + Graph Retrieval"]
    H --> P
    G --> Q["Qwen API"]
    K --> R
    R --> A
    A -->|"SSE 游标续接"| U
~~~

一次提问的主要过程如下：

1. API 校验账号、资源所有权、幂等键和限流，将用户消息与 run 写入 PostgreSQL。
2. API 把 run ID 推入 Redis 并返回 202，避免模型调用占用 HTTP 请求线程。
3. Worker 消费任务，LangGraph 完成问题分类、检索路由、回答生成与证据核验。
4. 检索层融合词法、向量和图关系结果；Qwen 仅依据候选证据组织回答。
5. Worker 持久化回答和 Agent 元数据，并把阶段事件写入 Redis Stream。
6. 浏览器通过 SSE 接收事件；刷新或短暂断线后可从游标继续读取。

更细的设计取舍见[项目实现细节与面试追问手册](docs/PROJECT_INTERVIEW_GUIDE.md)。

## 快速启动

### 使用 Docker Compose

需要 Docker 与 Docker Compose。默认配置不要求模型密钥，系统会使用可审计的抽取式降级回答。

~~~bash
docker compose -f deploy/compose/docker-compose.yml up --build
~~~

服务就绪后可访问：

- Web：http://localhost:3000
- OpenAPI：http://localhost:8000/docs
- 存活探针：http://localhost:8000/health/live
- 就绪探针：http://localhost:8000/health/ready

首次打开 Web 时创建普通账户。会话使用 HttpOnly Cookie 保存，历史对话按账户隔离。

停止服务：

~~~bash
docker compose -f deploy/compose/docker-compose.yml down
~~~

PostgreSQL 和 Redis 数据保存在命名卷中。只有确定要清空本地数据时才添加 `--volumes`。

### 接入千问与向量检索

复制 `.env.example` 为本地配置文件，至少填写以下变量：

~~~dotenv
AGENTIC_RAG_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_LLM_MODEL=qwen-plus
AGENTIC_RAG_LLM_API_KEY=replace-with-your-secret

AGENTIC_RAG_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_EMBEDDING_API_KEY=replace-with-your-secret
AGENTIC_RAG_EMBEDDING_MODEL=text-embedding-v4
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=shadow
~~~

使用配置启动并生成向量：

~~~bash
docker compose --env-file .env.local -f deploy/compose/docker-compose.yml up --build -d
docker compose --env-file .env.local -f deploy/compose/docker-compose.yml exec worker python -m agentic_rag.knowledge.ingest
~~~

先在 shadow 模式核对召回结果，再切换为 hybrid。配置说明和回滚方法见[向量检索实施手册](docs/VECTOR_RETRIEVAL_IMPLEMENTATION.md)，独立检索基线的千问调试方式见[V1 千问接入指南](docs/QWEN_API_SETUP.md)。

## 本地开发

后端需要 Python 3.12，前端需要 Node.js 22 和 pnpm 11。本地仍需可访问的 PostgreSQL 与 Redis。

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
alembic upgrade head
agentic-rag-api
~~~

另开终端启动 Worker 和 Web：

~~~powershell
.\.venv\Scripts\Activate.ps1
agentic-rag-worker
~~~

~~~powershell
pnpm install --frozen-lockfile
pnpm --dir apps/web dev
~~~

## 仓库结构

~~~text
agentic-rag/
├── apps/web/                 # Next.js 会话前端
├── src/agentic_rag/          # API、数据层、Worker、Agent 与 Dense Retrieval
├── src/agentic_rag_v1/       # 主链路复用的词法、Advanced RAG 与 GraphRAG 内核
├── migrations/               # Alembic 关系表与 pgvector 迁移
├── knowledge/                # 元数据 schema、manifest 规则和脱敏 fixture
├── eval/cases/               # 版本化效果测试集
├── tests/                    # 单元、集成、E2E 与检索回归测试
├── load/k6/                  # 模型链路和平台链路负载测试
├── deploy/                   # 镜像、Compose、Caddy 和 ECS 运维脚本
├── docs/                     # 架构、评测、部署、实施记录与面试手册
├── pyproject.toml            # Python 包、CLI 与质量工具配置
└── pnpm-workspace.yaml       # 前端工作区配置
~~~

`src/agentic_rag_v1` 名称保留是为了维持检索消融基线；它的 Advanced RAG 和 GraphRAG 模块仍被当前 Agent runtime 复用，并非废弃目录。完整职责划分见[项目结构说明](docs/PROJECT_STRUCTURE.md)。

## 测试与评测

本地质量检查：

~~~bash
ruff check src/agentic_rag tests/unit tests/integration
mypy src/agentic_rag tests/unit tests/integration tests/e2e
pytest -q tests/unit tests/test_agentic_rag_v1.py
python -m compileall -q src tests
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
docker compose -f deploy/compose/docker-compose.yml config --quiet
~~~

效果评测覆盖 Agent 路由、证据充分性、引用、URL 和渠道安全，以及 lexical、dense、hybrid 检索消融。负载测试把真实模型链路与不调用模型的平台链路分开，便于区分模型延迟和应用瓶颈。

项目已经在杭州 4 核 8 GB ECS 上完成单机部署、恢复演练与分层负载验证。20 用户平台目标门禁通过；100 VU 突发测试保持请求与业务检查正确，同时暴露出认证和读取延迟不适合作为稳态容量。详细结果及对外表述边界见[容量与效果基线](docs/PHASE2_CAPACITY_BASELINE.md)。

## 文档导航

| 阅读目标 | 文档 |
| --- | --- |
| 快速了解所有文档 | [文档中心](docs/README.md) |
| 准备项目面试 | [实现细节与面试追问手册](docs/PROJECT_INTERVIEW_GUIDE.md) |
| 理解 Agent 与工程主链路 | [Phase 2 实施记录](docs/PHASE2_IMPLEMENTATION.md) |
| 理解混合检索和 pgvector | [向量检索实施手册](docs/VECTOR_RETRIEVAL_IMPLEMENTATION.md) |
| 查看效果实验设计 | [RAG 效果评测](docs/RAG_EVALUATION.md) |
| 在阿里云 ECS 部署 | [ECS 部署手册](docs/ECS_DEPLOYMENT.md) |
| 查看安全与贡献约定 | [安全策略](SECURITY.md) · [贡献指南](CONTRIBUTING.md) |

## 数据与能力边界

- Git 只保存知识契约、非敏感 manifest 和脱敏 fixture。原始 PDF、聊天记录、生成索引与真实评测报告均被忽略。
- 当前采用普通账号，不接入学校统一身份认证。
- 学校资料通过受控摄取接口人工维护，尚未实现自动抓取和自动发布。
- 当前部署证据来自单机 ECS 和受控压测，不等同于真实生产用户运营记录。
- 公网演示地址不写死临时 IP；正式长期开放前还需域名、HTTPS、备案和持续运维方案。

项目对外统一表述为：按约 100 名注册用户、10 至 20 人在线的初版规模设计，并完成云端部署和分层压测验证。
