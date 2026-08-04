# PAIMON RAG 效果量化实验设计

本文档说明如何量化 PAIMON Next 的 RAG 效果，并生成可视化报告。

## 实验目标

回答三个问题：

1. 不使用 RAG 时，系统是否能给出可追溯答案？
2. 旧知识库与新知识库相比，召回能力提升多少？
3. 简单 BM25 与新版混合检索相比，来源命中和答案覆盖是否提升？

## 对比组

| 变体 | 含义 |
| --- | --- |
| `no_rag` | 不检索资料，作为下限基线 |
| `old_kb_hybrid` | 只使用旧知识库，采用混合检索 |
| `full_kb_bm25` | 使用完整知识库，只采用 BM25 |
| `full_kb_hybrid` | 使用完整知识库，采用 BM25 + ngram + RRF + 重排 |
| `full_kb_advanced` | 使用完整知识库，采用多查询融合、证据自检、纠错检索、来源权威度和多样性重排 |
| `full_kb_graphrag` | 使用完整知识库，采用 GraphRAG 社区摘要、实体扩展和 Advanced RAG 融合 |

实验默认不调用千问，不消耗 API 额度。它评估的是检索与证据抽取能力。

## 测试集

当前脚本内置 14 个问题，覆盖：

- 统一身份认证
- 校园卡
- 新生体检
- 学号查询
- 培养方案
- 校园网与路由器
- 社团介绍
- 辅修/转专业/分流
- 奖助学金
- 英语分级考试
- 开学物品清单
- 二次选拔
- 院系介绍
- 学生证与交通优惠

每个问题包含：

- 期望来源片段
- 期望答案关键词
- 领域分类

## 指标

| 指标 | 说明 |
| --- | --- |
| Top-1 来源命中率 | 第一条来源是否包含期望来源 |
| Top-3 来源命中率 | 前三条来源是否包含期望来源 |
| 答案关键词覆盖率 | 抽取式答案覆盖期望关键词的比例 |
| 平均置信度 | 检索分数派生的置信度，不等同于事实正确率 |
| 平均延迟 | 每个问题在该变体下的平均检索和回答耗时 |

## 运行方式

```bash
python -X utf8 -m paimon_next.evaluate --suite regression --output-dir reports --top-k 5
```

输出：

- `reports/rag_eval_*.json`
- `reports/rag_eval_*.html`

打开 HTML 文件即可查看柱状图、汇总表、失败样例和逐题结果。

## Harness 门禁

评测 case 已外置到 `eval/cases/`：

- `smoke.jsonl`：少量快速题，用于本地或 CI 的轻量门禁。
- `regression.jsonl`：完整回归题集，用于算法改动后的质量对比。

常用命令：

```bash
python -X utf8 -m paimon_next.evaluate --suite smoke --variants full_kb_bm25 --gate-variant full_kb_bm25 --min-top3 0.3 --min-keyword-coverage 0.5
python -X utf8 -m paimon_next.evaluate --suite regression --gate-variant full_kb_graphrag --min-top3 1.0 --min-keyword-coverage 0.95 --max-avg-latency-ms 9000
python -X utf8 -m paimon_next.evaluate --suite regression --baseline reports/rag_eval_20260605-123117.json --fail-on-regression
```

门禁失败时命令会非 0 退出，并在控制台与 HTML 报告中列出关注变体的失败样例、遗漏关键词和 Top 来源。

## 当前实验结果

本次实验时间：2026-06-05 12:31。

| 变体 | Top-1 | Top-3 | 关键词覆盖 | 平均延迟 |
| --- | ---: | ---: | ---: | ---: |
| `no_rag` | 0.0% | 0.0% | 0.0% | 0.0 ms |
| `old_kb_hybrid` | 50.0% | 64.3% | 78.6% | 77.0 ms |
| `full_kb_bm25` | 78.6% | 78.6% | 83.3% | 529.4 ms |
| `full_kb_hybrid` | 92.9% | 100.0% | 100.0% | 3386.6 ms |
| `full_kb_advanced` | 92.9% | 100.0% | 97.6% | 6031.3 ms |
| `full_kb_graphrag` | 92.9% | 100.0% | 97.6% | 7590.5 ms |

结论：

1. RAG 对可追溯回答是必要的，`no_rag` 在来源命中和关键词覆盖上均为 0。
2. 加入 `QQ/` 和 `Documents/` 后，知识覆盖明显提升。
3. 完整混合检索、高级 RAG、GraphRAG 的 Top-3 来源命中均达到 100%，GraphRAG 的 Top-1 与 Advanced RAG 持平。
4. GraphRAG 额外提供社区摘要和图谱扩展上下文，代价是平均延迟更高；后续应优化索引常驻、倒排索引缓存或引入图数据库/向量数据库。

## 工程建议

- 开发/论文实验：优先展示 `full_kb_graphrag`，同时保留 `full_kb_advanced` 和 `full_kb_hybrid` 作为消融对照。
- 线上高并发：可先用 `full_kb_bm25` 或 `full_kb_hybrid` 做候选召回，再对 Top-N 启用高级重排。
- 后续优化：将 BM25/ngram 统计缓存落盘，或接入 FAISS/Chroma/Milvus 与 cross-encoder reranker。
