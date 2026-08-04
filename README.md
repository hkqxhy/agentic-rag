# PAIMON

PAIMON 是一个面向南京大学新生事务的、强调证据边界与可追溯引用的智能助手。本仓库正在从本地 RAG 原型迁移为可部署、可评测、可扩展的 Agentic RAG 工程。

> 当前状态：`paimon_next` 是可运行的 V1 基线；LangGraph Agent、Hybrid RAG、持久会话、独立 Web 前端和生产部署属于 V2 重做范围。仓库不会把规划中的能力描述为已经完成。

## 当前可运行能力

- BM25 + 字符 n-gram 混合召回、RRF 融合和轻量重排；
- 检索证据质量判断、低置信度澄清和过期资料提醒；
- 可选 GraphRAG 增强；
- 带 `[S1]` 等来源编号的回答；
- CLI、HTTP API、SSE 流式输出和旧版 `PAIMON().rag()` 兼容入口；
- 无外部模型时的抽取式降级；
- 小型 smoke/regression 评测和 HTML 报告生成。

V2 的完整目标、技术选型和阶段门禁见 [工程化重做方案](docs/REBUILD_PLAN_V1.md)。

## 快速开始

要求 Python 3.12 或更高版本。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env.local
```

Linux：

```bash
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env.local
```

新克隆的仓库只包含脱敏测试资料，可以直接验证完整链路：

```bash
python -m paimon_next.cli --json "这是真实的学校通知吗？"
```

启动 V1 HTTP API：

```bash
python PAIMON.py --host 127.0.0.1 --port 8002
```

健康检查：`GET http://127.0.0.1:8002/health`。

## 模型配置

复制 `.env.example` 为 `.env.local` 后设置 OpenAI-compatible 模型网关：

```dotenv
PAIMON_LLM_BASE_URL=https://your-compatible-endpoint/v1
PAIMON_LLM_MODEL=qwen-plus
PAIMON_LLM_API_KEY=your-secret
```

不配置 `PAIMON_LLM_BASE_URL` 时，V1 自动使用抽取式回答。阿里云百炼接入说明见 [QWEN_API_SETUP.md](docs/QWEN_API_SETUP.md)。真实密钥只能放在 `.env.local` 或部署平台的 Secret 中。

## 知识库边界

源码仓库只保存：

- `knowledge/schemas/`：知识元数据契约；
- `knowledge/manifests/`：不含敏感内容的来源与版本元数据；
- `knowledge/fixtures/`：明确标注为非生产资料的脱敏测试样例。

原始 PDF、QQ 记录、解析产物、向量索引和评测报告不进入 Git。它们保留在本机或后续对象存储中，通过 `PAIMON_SOURCE_PATHS` 或知识摄取接口接入。详细规则见 [knowledge/README.md](knowledge/README.md)。

## 验证

```bash
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q PAIMON.py paimon_next tests
python -X utf8 -m paimon_next.evaluate --suite smoke --output-dir reports
```

`reports/` 是生成产物，不进入 Git。评测方法见 [RAG_EVALUATION.md](docs/RAG_EVALUATION.md)。

## 仓库结构

```text
paimon_next/            V1 基线实现
PAIMON.py               V1 API 与旧调用兼容入口
tests/                  单元与回归测试
eval/cases/             版本化评测样例
knowledge/              schema、manifest 规则和脱敏 fixture
docs/                   架构、部署、评测与交付方案
legacy/prototype/       早期实验代码，不属于支持运行时
```

完整发布边界和 V2 迁移规则见 [PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)。

## 已确认的 V2 首版约束

- 约 100 名注册用户，峰值 10—20 人在线；
- 阿里云 ECS 4 核 8 GB 用于开发、集成、压测和秋招演示；
- 云端通义千问 API，不在 CPU ECS 上部署 vLLM；
- 普通账号体系；
- 学校资料首版通过人工维护和受控接口更新，不实现自动爬虫。

部署步骤见 [CLOUD_DEPLOYMENT_GUIDE.md](docs/CLOUD_DEPLOYMENT_GUIDE.md)，秋招交付路线见 [AUTUMN_RECRUITMENT_DELIVERY_PLAN.md](docs/AUTUMN_RECRUITMENT_DELIVERY_PLAN.md)。

## 安全说明

发现密钥泄漏、个人信息暴露、越权访问或提示词注入问题时，请不要创建公开 Issue。处理方式见 [SECURITY.md](SECURITY.md)。
