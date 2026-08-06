# Phase 2 实施记录

## 当前切片：Phase 2.1

Phase 2.1 的目标是先让真实 Agent/RAG 主链路穿过现有生产形态，而不是另建一个只能本地运行的演示入口。

```text
用户消息 -> PostgreSQL run -> Redis queue -> Worker
  -> LangGraph normalize/classify
  -> direct | clarify | fast_rag | research_rag
  -> Advanced/Graph RAG -> Qwen 或抽取式降级
  -> evidence verify -> SSE + PostgreSQL message metadata
```

每次回答保存 `framework`、`graph_version`、路由、意图、置信度、grounded 标记、节点轨迹、来源、警告和检索诊断。前端通过同一消息接口恢复答案和来源，不依赖 Worker 内存。

## 千问启用方式

在不提交到 Git 的 `deploy/env/staging.env` 中设置：

```dotenv
AGENTIC_RAG_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_LLM_MODEL=qwen-plus
AGENTIC_RAG_LLM_API_KEY=你的百炼API-Key
```

不设置 URL 或 Key 时，系统仍会执行完整检索与证据核验，并使用抽取式回答，方便无模型成本地部署和回归测试。

部署后可在 Worker 容器内执行真实模型效果冒烟测试：

```bash
sudo docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T worker python -m agentic_rag.agent.evaluate --require-llm
```

测试覆盖 Agent 路由、grounded 判定、引用、无关问题拒答、URL 证据一致性、千问实际调用成功率以及总体/LLM p50、p95 延迟。只有 `generation_mode` 为 `llm` 才算千问调用成功；调用失败或生成知识片段中不存在的 URL 时会触发抽取式降级，不会被误报为模型成功。

## 下一切片

1. 建立可版本化的知识摄取、预览、发布、回滚和增量索引任务；
2. 引入独立稠密向量检索与 reranker，并与当前检索做消融对比；
3. 扩充黄金测试集，增加 groundedness、citation precision、retrieval recall、拒答和延迟/成本指标；
4. 在 ECS 配置千问 Key 后重新执行功能、10/20 并发和稳定性测试。
