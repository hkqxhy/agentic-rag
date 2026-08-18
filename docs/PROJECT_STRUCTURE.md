# 项目结构与发布边界

Agentic RAG 按“产品入口、应用服务、检索内核、数据治理、质量验证、部署运维”拆分。根目录只保留工作区配置和面向贡献者的文档，运行代码全部进入 apps 或 src。

## 当前结构

~~~text
agentic-rag/
├── apps/
│   └── web/                    # Next.js App Router 会话产品
├── src/
│   ├── agentic_rag/            # 当前 API、Worker、Agent、数据层和向量检索
│   └── agentic_rag_v1/         # 被当前 runtime 复用的检索与评测内核
├── migrations/                 # Alembic 关系模型与 pgvector 迁移
├── knowledge/
│   ├── fixtures/               # 脱敏的最小演示知识
│   ├── manifests/              # 知识来源与版本约定
│   └── schemas/                # 文档元数据 JSON Schema
├── eval/cases/                 # 版本化效果测试集
├── tests/
│   ├── unit/                   # 纯逻辑与契约测试
│   ├── integration/            # PostgreSQL、pgvector、Redis 和 Worker
│   └── e2e/                    # 已部署环境 API 验收
├── load/k6/                    # 模型链路与平台链路负载测试
├── deploy/
│   ├── compose/                # 本地、预生产和生产 Compose 模型
│   ├── docker/                 # API、Worker 和 Web 镜像定义
│   ├── caddy/                  # 同源反向代理配置
│   ├── ecs/                    # 部署、入库、恢复和压测脚本
│   └── env/                    # 不含密钥的环境变量模板
├── docs/                       # 架构、部署、评测、实施记录和面试手册
├── .github/workflows/          # CI 与镜像同步工作流
├── pyproject.toml              # Python 包、CLI 和质量门禁
└── pnpm-workspace.yaml         # 前端 workspace
~~~

## 模块边界

- `apps/web` 只通过 HTTP 和 SSE 与后端交互，不直接访问数据库或 Redis。
- `src/agentic_rag/api` 负责认证、所有权、幂等、限流和任务受理，不执行长时间模型推理。
- `src/agentic_rag/worker.py` 消费任务并驱动 Agent，把状态写入 PostgreSQL 和 Redis Stream。
- `src/agentic_rag/agent` 定义有界状态图、路由、回答生成和证据核验。
- `src/agentic_rag/knowledge` 负责 Embedding、pgvector 检索、知识入库和检索评测。
- `src/agentic_rag_v1` 保存词法、Advanced RAG 和 GraphRAG 内核。名称用于维持消融基线，当前主链路仍会复用这些模块。

早期、机器相关且不参与构建的实验脚本已经从主分支删除。需要研究项目演进时，以 `docs/REBUILD_PLAN_V1.md` 和 Git 历史为准。

## 不进入 Git 的内容

~~~text
Documents/                   原始 PDF 等资料
QQ/                          原始聊天记录
data/                        本地原始文档
.agentic_rag_v1_index/       生成的词法索引
reports/                     评测和压测输出
knowledge/raw/               原始知识区
knowledge/normalized/        标准化数据区
knowledge/artifacts/         chunks、embedding 和 graph 产物
~~~

## 提交前检查

- 本地配置、日志、索引、报告和原始知识文件未被跟踪。
- 代码、文档和 Git 历史新增内容中没有真实 API key、令牌或私钥。
- 后端 lint、类型检查、单元测试与 compileall 通过。
- 前端 lint、类型检查与生产构建通过。
- Compose 模型可解析，涉及数据模型时迁移能够在空库和已有库执行。
- 文档清楚区分已实现能力、测试边界和后续规划。
