# PAIMON GraphRAG 说明

当前知识库已经加入本地 GraphRAG 层。它不是替换原来的 Advanced RAG，而是在原有检索之上增加“实体/主题图谱 + 社区摘要 + 图扩展召回”。

## 它做了什么

GraphRAG 会从现有 `KnowledgeChunk` 中抽取：

- 主题/实体节点：如校园卡、统一身份认证、社团、培养方案、医保、校园网等。
- 社区节点：按资料目录和来源聚合，如 `南大社团介绍`、`各院系培养方案`、`奖助学金`、`QQ咨询沉淀`。
- 关系边：同一知识块中共同出现的主题会建立共现关系。
- 社区摘要：为每个社区生成一个可检索的摘要知识块。

生成的图谱缓存为：

```text
.paimon_index/graph.json
```

## 问答链路

现在默认链路是：

```text
用户问题
  -> GraphRAG 图谱搜索
  -> Advanced RAG 多查询检索
  -> 图谱社区/实体 boost 融合
  -> 证据自检
  -> 千问生成或本地抽取式回答
```

总览型问题会优先看到社区摘要，例如：

```text
南大有哪些社团可以参加？
```

具体办事问题仍会优先返回原始 QA/文档，例如：

```text
校园卡丢了怎么补办？
```

## 接口变化

`/ask`、`/ask/stream`、`/RAG/chat` 的 `diagnostics.mode` 现在默认为：

```json
"graph_rag"
```

并包含图谱诊断：

```json
{
  "diagnostics": {
    "mode": "graph_rag",
    "graph": {
      "terms": 46032,
      "communities": 17,
      "matched_terms": ["社团", "南大"],
      "matched_communities": ["南大社团介绍", "社团宣传资料"]
    },
    "graph_hit_count": 2,
    "graph_boosted_hits": 1
  }
}
```

`GET /health` 也会返回：

```json
{
  "rag_mode": "graph_rag",
  "graph_terms": 46032,
  "graph_communities": 17
}
```

## 使用方式

默认启用：

```bash
python PAIMON.py --host 127.0.0.1 --port 8002
```

重建知识库和图谱：

```bash
python -m paimon_next.cli --reindex "南大有哪些社团可以参加？"
```

临时关闭 GraphRAG，回退到 Advanced RAG：

```bash
set PAIMON_USE_GRAPHRAG=0
python PAIMON.py --host 127.0.0.1 --port 8002
```

## 工程取舍

当前版本是轻量本地图谱，不依赖 Neo4j、NetworkX、LLM 实体抽取或外部向量数据库。好处是可直接部署、可缓存、可测试；代价是实体抽取还偏规则化。

后续可以继续升级：

1. 用千问或专门的信息抽取模型做实体/关系抽取。
2. 接入 Neo4j 或 NetworkX 做更强的社区发现。
3. 为每个社区生成 LLM 摘要，并保留版本号和人工审核。
4. 把 GraphRAG 作为总览问题的主路径，把 Advanced RAG 作为精确办事问题的主路径。
