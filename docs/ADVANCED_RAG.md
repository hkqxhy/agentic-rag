# Agentic RAG Advanced RAG 升级说明

本次升级把项目从“基础检索 + 生成”的 RAG，推进到更适合新生问答场景的证据驱动型 RAG。核心目标不是堆模型，而是在校园垂直场景里让系统更会找、更敢说不知道、更容易解释为什么这么答。

## 已落地能力

### 1. 新生场景 Query Planner

新增 `agentic_rag_v1/advanced.py`，会先判断问题意图，再生成多个查询变体：

- 原始问题
- 意图扩展词，如校园卡、医保、选课、转专业、社团、校园网
- 场景路由词，如 `南大社团介绍`、`各院系培养方案`、`校园网相关`
- 流程型问题扩展为“流程、材料、步骤、注意事项”
- 总览型问题扩展为“目录、清单、汇总、介绍”

这样可以覆盖新生常见的口语问法、简称问法和资料目录型问题。

### 2. RAG-Fusion 多路召回

每个查询变体都会调用原有 Hybrid Retriever，然后用 Reciprocal Rank Fusion 思路融合排序。相比只搜一次，优势是：

- “校园卡丢了怎么办”能同时召回挂失、补办、充值入口等相关表述
- “有哪些社团”能召回目录知识块和具体社团文件
- “培养方案”能跨 PDF、目录、院系文档互相补强

### 3. CRAG/Self-RAG 风格证据自检

系统会为每次检索生成 `diagnostics`：

- `quality`：综合证据质量分
- `sufficient`：是否足以回答
- `corrective_pass`：是否执行过纠错检索
- `coverage`：问题词覆盖率
- `authority`：来源权威度
- `source_diversity`：来源多样性
- `reasons`：低分原因，如 `low_query_coverage`、`weak_source_authority`、`possibly_stale_evidence`

当第一轮证据不足时，会自动追加“南京大学 新生 / 官方通知 / 常见问题 / 场景路由词”等纠错查询再检索。若仍不足，服务层会走澄清/核验分支，而不是强行生成答案。

### 4. 来源权威度与时效性建模

检索重排不再只看文本相似度，而是引入校园知识库里的工程信号：

- `Documents/`、PDF、手册、通知、办法、指南、培养方案优先级更高
- `南哪QA.qa` 作为结构化问答有额外加权
- `QQ/` 群聊资料保留召回价值，但权威度低于正式文档
- 含历史年份、旧通知、失效说明的资料会轻微降权并触发提醒

这对新生问答很重要，因为错把旧群聊、旧年份安排当成当前事实，会造成真实办事风险。

### 5. 证据多样性与压缩

最终 Top-K 会做来源去冗余，避免同一个文件或同一路径的相似片段刷屏。LLM 侧只看到更紧凑的证据包，以及一条 `[DIAGNOSTICS]` 诊断块，用来指导模型更保守地生成带引用答案。

### 6. 可观测接口

`/ask`、`/ask/stream`、`/RAG/chat` 返回值新增：

```json
{
  "diagnostics": {
    "mode": "advanced_rag",
    "quality": 0.61,
    "sufficient": true,
    "corrective_pass": false,
    "authority": 0.74,
    "coverage": 0.38,
    "source_diversity": 0.8,
    "reasons": ["evidence_passed"]
  }
}
```

前端流式页面会显示 confidence、intent、RAG mode、quality，以及是否触发纠错检索。

## 竞争力定位

Agentic RAG 的差异化不应是“又一个通用聊天 RAG”，而是“面向新生真实办事的校园证据助手”：

1. 能处理校园知识的多源异构性：正式 PDF、指南、目录、QA、群聊沉淀。
2. 能区分“能答”和“该核验”：对年份、费用、时间、系统入口保持保守。
3. 能解释检索过程：每次回答都有诊断，便于运营者排查知识库缺口。
4. 能按成本分级落地：词法基线不依赖模型；生产链路可启用 `text-embedding-v4` + pgvector Dense 召回，并通过 `off -> shadow -> hybrid` 灰度开关与自动降级控制风险。
5. 能围绕新生场景持续进化：意图、路由词、权威来源规则都可以低成本迭代。

## 如何运行

```bash
python agentic_rag_v1_server.py --host 127.0.0.1 --port 8002
```

打开：

```text
http://127.0.0.1:8002/
```

普通请求：

```bash
curl -X POST http://127.0.0.1:8002/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"南大有哪些社团可以参加？\"}"
```

流式请求：

```bash
curl -N -X POST http://127.0.0.1:8002/ask/stream ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"宿舍校园网和路由器怎么用？\"}"
```

## 如何量化

评测脚本已加入 `full_kb_advanced` 对照组：

```bash
python -X utf8 -m agentic_rag_v1.evaluate --output-dir reports --top-k 5
```

报告会比较：

- `no_rag`
- `old_kb_hybrid`
- `full_kb_bm25`
- `full_kb_hybrid`
- `full_kb_advanced`

重点看 Top-1 来源命中率、Top-3 来源命中率、答案关键词覆盖率和平均延迟。

## 后续增强建议

1. 当前已接入中文 embedding + pgvector，并用 Weighted RRF 把 lexical advanced RAG 升级为 hybrid dense/sparse RAG；下一步是在 ECS 完成 Shadow 消融评测，再决定是否启用 cross-encoder reranker。
2. 把 `Documents/` 里的院系、校区、事务流程做成轻量知识图谱，用 GraphRAG 风格的社区摘要回答总览型问题。
3. 增加人工审核台，对 `possibly_stale_evidence`、低 quality 问题沉淀为知识库更新任务。
4. 建立新生高频问题黄金评测集，按身份认证、报到、医保、校园卡、选课、住宿、交通、社团分层统计。
5. 对接官方通知源，做定时抓取与“最新事实覆盖旧事实”的版本策略。
