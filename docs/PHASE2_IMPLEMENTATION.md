# Agent 与混合检索实施记录

本阶段的目标是让真实 Agent/RAG 链路运行在可部署的会话系统内，并为向量检索提供可灰度、可评测、可回滚的工程路径。

## 当前主链路

~~~text
用户消息 -> PostgreSQL message / run -> Redis queue -> Worker
  -> LangGraph normalize / classify
  -> direct | clarify | fast_rag | research_rag
  -> BM25 + n-gram + Dense + GraphRAG
  -> Qwen 或抽取式降级
  -> evidence verify -> Redis Stream + PostgreSQL metadata
  -> SSE -> Next.js
~~~

每次回答保存 framework、graph version、路由、意图、置信度、grounded 标记、节点轨迹、来源、警告、安全过滤和检索诊断。前端从消息接口恢复答案与引用，不依赖 Worker 内存。

## LangGraph 节点

| 节点 | 职责 |
| --- | --- |
| normalize | 清理问题并建立稳定的状态字段 |
| classify | 判断问候、信息缺失、普通检索和复杂检索 |
| direct | 处理无需知识库的确定性回复 |
| clarify | 在问题或证据不足时请求补充信息 |
| rag | 执行检索、融合、生成或抽取式降级 |
| verify | 检查 grounded、引用、URL 和高风险渠道 |

状态图是有界的，每条路径都有终止节点。节点轨迹会写入消息元数据，便于测试和线上诊断。

## 千问配置

在不提交到 Git 的环境文件中设置：

~~~dotenv
AGENTIC_RAG_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_LLM_MODEL=qwen-plus
AGENTIC_RAG_LLM_API_KEY=replace-with-your-secret
~~~

不设置 URL 或 Key 时，系统仍执行完整检索与证据核验，并使用抽取式回答。

真实模型效果门禁：

~~~bash
python -m agentic_rag.agent.evaluate --require-llm
~~~

该命令检查 Agent 路由、grounded 判定、引用、无关问题拒答、URL 与高风险渠道一致性、千问调用成功率，以及总体和模型调用延迟。

## pgvector 混合检索

关系库使用 PostgreSQL 17 和 pgvector。知识文档、Chunk 与 Embedding 分表保存，Embedding 使用内容哈希和版本字段判断是否需要重新生成。HNSW 余弦索引用于候选召回。

~~~dotenv
AGENTIC_RAG_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_EMBEDDING_API_KEY=replace-with-your-secret
AGENTIC_RAG_EMBEDDING_MODEL=text-embedding-v4
AGENTIC_RAG_EMBEDDING_DIMENSIONS=1024
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=shadow
~~~

完成迁移后执行入库与检索消融：

~~~bash
python -m agentic_rag.knowledge.ingest
python -m agentic_rag.knowledge.evaluate --suite smoke
~~~

发布顺序为 off、shadow、hybrid。shadow 阶段会执行向量检索并记录结果，但回答仍沿用原词法链路；确认召回收益、延迟和失败回退后再启用 hybrid。详细表结构、ECS 镜像切换和回滚命令见[向量检索实施手册](VECTOR_RETRIEVAL_IMPLEMENTATION.md)。

## 负载验证

真实模型探针：

~~~bash
sudo VUS=2 ITERATIONS=2 E2E_P95_MS=20000 \
  bash deploy/ecs/verify-phase2-model-load.sh

sudo VUS=5 ITERATIONS=1 E2E_P95_MS=30000 \
  bash deploy/ecs/verify-phase2-model-load.sh
~~~

平台旅程测试把注册、会话恢复、列表、创建、读取、重命名和删除串成一轮，不调用模型，用来观察应用与数据层。

ECS 优先使用宿主机 k6，找不到时才使用 Docker 镜像。中国大陆网络环境可通过 APT 安装 k6，避免 Docker Hub 镜像同步延迟。具体实测值和解释边界见[容量与效果基线](PHASE2_CAPACITY_BASELINE.md)。

## 后续工作

1. 扩充正式黄金测试集，覆盖更多校区、年级、时效冲突和拒答样例。
2. 补齐管理员知识上传、预览、审核、发布和回滚界面。
3. 在 Dense 收益稳定后评估 qwen3-rerank，使用消融结果决定是否增加调用成本。
4. 域名、HTTPS 和备案条件具备后，再开放长期公网演示地址。
