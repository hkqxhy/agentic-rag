# 文档中心

这里集中保存 Agentic RAG 的架构决策、实现记录、部署步骤、评测证据和技术解析。第一次了解项目时，建议先读根目录 README，再按目标选择下列文档。

## 推荐阅读路线

### 10 分钟了解项目

1. [项目结构与模块边界](PROJECT_STRUCTURE.md)
2. [Phase 2 Agent 主链路实施记录](PHASE2_IMPLEMENTATION.md)
3. [正式知识目录与维护规范](../knowledge/official/README.md)
4. [pgvector 语义检索实施手册](VECTOR_RETRIEVAL_IMPLEMENTATION.md)
5. [容量与效果基线](PHASE2_CAPACITY_BASELINE.md)

### 深入理解

1. [技术实现深度解析](TECHNICAL_DEEP_DIVE.md)
2. [创新点与工程落地说明](INNOVATIONS.md)
3. [RAG 效果实验设计](RAG_EVALUATION.md)

### 部署与运维

1. [公有云部署与千问接入方案](CLOUD_DEPLOYMENT_GUIDE.md)
2. [阿里云 ECS 部署手册](ECS_DEPLOYMENT.md)
3. [杭州 ECS 预生产部署手册](ECS_STAGING_DEPLOYMENT.md)
4. [V1 检索基线的千问 API 调试](QWEN_API_SETUP.md)

## 文档分类

| 类别 | 文档 | 用途 |
| --- | --- | --- |
| 架构 | [Advanced RAG](ADVANCED_RAG.md) | 查询规划、多路召回、证据自检和重排 |
| 架构 | [GraphRAG](GRAPHRAG.md) | 轻量图结构、社区和查询增强 |
| 架构 | [向量检索](VECTOR_RETRIEVAL_IMPLEMENTATION.md) | pgvector 数据模型、增量入库、灰度和回滚 |
| 数据治理 | [正式知识目录](../knowledge/official/README.md) | 官方来源、适用边界、发布门禁与人工维护流程 |
| 工程 | [项目结构](PROJECT_STRUCTURE.md) | 目录职责、发布边界和本地忽略内容 |
| 工程 | [Phase 1 实施记录](PHASE1_IMPLEMENTATION.md) | 会话产品、异步链路和部署骨架 |
| 工程 | [Phase 2 实施记录](PHASE2_IMPLEMENTATION.md) | LangGraph、真实模型和检索接入 |
| 评测 | [RAG 评测](RAG_EVALUATION.md) | 数据集、指标、消融和门禁方法 |
| 评测 | [云端综合效果评测](STAGING_EFFECT_EVALUATION.md) | 48 类线上黑盒用例、分层指标与报告解读 |
| 评测 | [Phase 1 验收报告](PHASE1_ACCEPTANCE_REPORT.md) | 基础工程验收证据 |
| 评测 | [Phase 2 容量基线](PHASE2_CAPACITY_BASELINE.md) | 真实模型与平台负载结果 |
| 部署 | [ECS 部署](ECS_DEPLOYMENT.md) | 正式环境配置、上线、验收和回滚 |
| 历史 | [工程化重做方案](REBUILD_PLAN_V1.md) | 从原型到当前架构的设计依据 |

## 状态说明

文件名中的 Phase 用于保留实施和验收时间线，不代表当前代码仍停留在对应阶段。根目录 README 描述当前能力；历史文档中的规划项应结合最新实施记录判断。容量数据只代表指定实例、指定脚本和指定时间的测试结果。
