# Agentic RAG 新生智能助手：技术实现深度解析

> 本文按当前仓库实际代码整理实现细节和设计取舍，重点解释为什么这样设计、数据如何流动、失败时如何处理，以及哪些能力已经实现。

## 1. 项目一句话定义

Agentic RAG 是一个面向高校新生事务的证据约束型问答系统。系统使用 LangGraph 对问题进行有界路由，使用 Advanced RAG 与轻量 GraphRAG 检索校园资料，调用阿里云百炼千问生成带引用回答，并通过 FastAPI、PostgreSQL、Redis、独立 Worker、SSE、Next.js、Caddy 和 Docker Compose 部署到阿里云 ECS。

项目解决的不是“让大模型无所不知”，而是以下三个更具体的问题：

1. 新生问题分散在通知、手册、PDF、FAQ、网页摘录和聊天沉淀中，检索成本高。
2. 时间、费用、入口和办理材料可能过期，模型依靠常识补全会产生真实办事风险。
3. 一个比赛原型要真正上线，需要账号、持久会话、任务队列、断线恢复、限流、审计、部署、测试和容量验证。

## 2. 实现边界

### 2.1 已经实现

- LangGraph 有界状态图：normalize、classify、direct、clarify、safe_refusal、out_of_scope、rag、verify。
- 六种路由：直接回答、歧义澄清、安全拒绝、领域外边界、快速 RAG、研究型 RAG。
- 多格式资料加载：QA/JSON、Markdown、TXT、CSV、DOCX、PDF。
- BM25、字符 n-gram 与 pgvector Dense 混合召回、RRF 融合、领域重排。
- 多查询改写、纠错检索、来源权威度、时效性、证据密度和多样性诊断。
- 轻量 GraphRAG：主题节点、共现边、目录社区、社区摘要和图扩展加权。
- 阿里云百炼 qwen-plus 的 OpenAI-compatible 接入。
- 模型不可用时的抽取式降级。
- 引用检查、无证据 URL 检测、高风险官方渠道检测和安全截断。
- 普通账号、Argon2 密码哈希、HttpOnly Cookie、所有权隔离和审计事件。
- PostgreSQL 持久会话、消息、Agent Run 和运行元数据。
- Redis 队列、Redis Stream 事件、取消标记和 Lua 原子限流。
- Next.js ChatGPT 风格界面、会话历史、重命名、删除、搜索、SSE 增量展示和刷新恢复。
- Docker Compose、Caddy、Alembic、健康检查、GitHub Actions CI 和阿里云 ECS 部署。
- 单元测试、集成测试、远程 E2E、RAG 评测和 k6 容量测试。

### 2.2 尚未实现或不能夸大的部分

- pgvector、`text-embedding-v4` 和 hybrid 模式已在 ECS 完成迁移与小规模模型链路验证；当前正式语料扩容后仍需重新生成向量并重跑同一评测集，不能沿用 fixture 阶段的效果结论。
- 当前没有 cross-encoder reranker，重排是可解释的领域特征加权。
- 当前 GraphRAG 不是微软 GraphRAG 的完整复刻，不依赖 Neo4j，也没有用 LLM 抽取实体关系或生成正式社区报告。
- 已接入首批经南京大学官方页面核验的结构化摘要，覆盖校园卡、身份认证、校园网、图书馆、2026 新生报到与选课、缴费资助等；体检医保、分校区入住材料和快递地址仍有明确缺口。
- 知识摄取、审核、发布、回滚的完整管理后台仍是后续工作；目前实现了文件加载、缓存重建和元数据契约。
- 生产 Worker 调用的是非流式模型接口。SSE 传输是真实的，但回答是在模型完整返回后由 Worker 分片推送，不是原生 token streaming。
- 当前 Redis List 使用 BLPOP，不具备消息确认和 visibility timeout。Worker 在特定崩溃窗口可能留下丢失或卡住的任务，需要后续升级 Redis Streams Consumer Group 或成熟任务队列。
- 100 虚拟用户测试证明了零请求失败，但认证和读取 P95 超过严格门槛，不能表述为“100 并发完全通过”。

对外介绍项目时应主动说明这些边界，避免把预留接口或测试环境描述成正式生产能力。

## 3. 总体架构

~~~mermaid
flowchart LR
    U[浏览器用户] --> C[Caddy 同源网关]
    C --> W[Next.js Web]
    C --> A[FastAPI API]
    A --> P[(PostgreSQL)]
    A --> R[(Redis)]
    R --> K[独立 Worker]
    K --> G[LangGraph Agent]
    G --> Q[Advanced RAG + GraphRAG]
    Q --> KB[知识文件与本地索引]
    G --> L[阿里云百炼 qwen-plus]
    K --> P
    K --> R
    R --> A
    A -->|SSE| C
    C --> U
~~~

各组件职责如下：

| 组件 | 职责 | 为什么单独存在 |
| --- | --- | --- |
| Next.js | 登录、对话历史、输入框、增量回答和来源展示 | 负责产品交互，不承担模型计算 |
| Caddy | 同源反向代理、压缩、安全响应头、生产 TLS | 避免前后端跨域复杂度，统一公网入口 |
| FastAPI | 鉴权、会话 CRUD、创建 Run、SSE、取消和健康检查 | 保持 API 轻量，不同步阻塞等待模型 |
| PostgreSQL | 用户、会话、消息、Run、审计记录 | 保存业务事实，支持重启恢复和关系约束 |
| Redis | 任务队列、事件流、限流计数器、取消标记 | 承担短生命周期、高频、可过期的协调数据 |
| Worker | 消费 Run、执行 Agent/RAG、写回结果和发布事件 | 隔离慢模型调用，允许独立扩容 |
| LangGraph | 控制问题处理路径 | 比自由 ReAct 更容易审计、测试和限制成本 |
| RAG | 检索、证据评分、纠错和引用 | 把回答约束在知识证据范围内 |
| 百炼千问 | 将检索证据组织成自然语言答案 | ECS 不承担大模型推理成本 |

## 4. 一条消息的完整生命周期

~~~mermaid
sequenceDiagram
    participant B as Browser
    participant API as FastAPI
    participant PG as PostgreSQL
    participant Redis as Redis
    participant Worker as Worker
    participant Agent as LangGraph/RAG
    participant Qwen as qwen-plus

    B->>API: POST /conversations/{id}/messages + Idempotency-Key
    API->>Redis: Lua 限流
    API->>PG: 写 user message 与 queued run
    API->>Redis: RPUSH run_id
    API-->>B: 202 + run_id
    B->>API: GET /runs/{run_id}/events
    API->>Redis: XREAD Redis Stream
    Worker->>Redis: BLPOP run_id
    Worker->>PG: queued -> running 原子 claim
    Worker->>Redis: XADD run.status
    Worker->>Agent: invoke(question, conversation_id)
    Agent->>Agent: normalize -> classify -> rag -> verify
    Agent->>Qwen: OpenAI-compatible chat request
    Qwen-->>Agent: 完整答案
    Worker->>Redis: XADD agent.step / message.delta
    Redis-->>API: event stream
    API-->>B: SSE events
    Worker->>PG: 写 assistant message + metadata
    Worker->>PG: running -> completed
    Worker->>Redis: XADD message.completed
    API-->>B: SSE completed
~~~

### 4.1 为什么 API 返回 202，而不是直接返回答案

202 Accepted 表示请求已被系统接受，但处理尚未完成。模型调用可能需要数秒甚至更久，如果 API 一直占用普通请求连接：

- API 并发能力会被慢模型拖垮。
- 浏览器超时后难以恢复任务状态。
- API 重启会丢失内存中的处理上下文。
- 模型和 Web 层不能独立扩容。

当前设计先把用户消息和 Run 写入 PostgreSQL，再把 run_id 放入 Redis。浏览器拿到 run_id 后订阅 SSE。这样 API 只负责受理和事件转发，Worker 承担慢任务。

### 4.2 为什么先写数据库，再入队

数据库是业务事实来源。先写 user message 和 queued run，可以保证：

- 队列里只有可查询的 run_id。
- Worker 拿到任务后能够从数据库恢复输入。
- 即使入队失败，也可以把 queued Run 标成 failed 并返回 503。
- 用户刷新后仍能看到已提交的消息和运行状态。

可以把它概括为：**PostgreSQL 保存事实，Redis 负责协调。**

## 5. LangGraph Agent 设计

核心代码位于 src/agentic_rag/agent/runtime.py。

### 5.1 为什么是“有界 Agent”

项目没有使用无限循环的 ReAct Agent，而是使用固定节点和条件边：

~~~text
START
  -> normalize
  -> classify
      -> direct
      -> clarify
      -> safe_refusal
      -> out_of_scope
      -> rag
  -> verify
  -> END
~~~

有界状态图的优势：

1. **成本可预测**：一次请求最多经过固定节点，不会无限工具调用。
2. **行为可测试**：可以直接断言问题走了哪条路。
3. **风险可控制**：所有回答最终经过 verify。
4. **便于观测**：每个节点都向 trace 追加结构化记录。
5. **适合校务场景**：这个场景的工具范围有限，不需要开放式自主规划。

对于“这算不算真正的 Agent”这个问题，可以这样解释：

> Agent 的关键不在于是否无限循环，而在于系统是否根据状态选择不同处理路径，并保留决策过程。这里使用 LangGraph 建模路由、澄清、检索和验证，是受约束的 workflow agent。它牺牲部分自由度，换取成本、正确性和可审计性。

### 5.2 AgentState

状态中保存：

- question：原问题。
- conversation_id：会话标识，用于检索追问上下文。
- normalized_query：标准化后的查询。
- route：direct、clarify、safe_refusal、out_of_scope、fast_rag、research_rag。
- intent：身份认证、校园卡、报到、宿舍等领域意图。
- answer：候选答案。
- confidence：检索派生置信度。
- sources：结构化来源。
- warnings：过期、链接核验和证据不足提醒。
- diagnostics：检索与生成诊断。
- grounded：回答是否有可验证证据。
- need_clarification：是否需要用户补充。
- trace：节点轨迹。

TypedDict(total=False) 允许节点只返回自己更新的字段，由 LangGraph 合并到整体状态。

### 5.3 normalize 节点

normalize_text 会执行：

- HTML entity 解码。
- br 标签转换行。
- 移除 Markdown 图片。
- 将 Markdown 链接保留为“文字 + URL”。
- 清除 HTML 标签。
- 合并多余空白。

标准化的意义是降低来源格式和用户输入格式对检索的干扰。

### 5.4 classify 节点

当前路由是确定性规则：

- 空问题、短输入或已知指代不清问题：clarify。
- “你好”“你是谁”等固定问候：direct。
- 请求系统提示词、密钥、其他用户数据或要求编造网址/群号：safe_refusal。
- 医疗诊断、股票、天气等非新生校务问题：out_of_scope。
- 包含“比较、区别、全部、汇总、流程、为什么”等复杂度词：research_rag。
- 其他校务问题：fast_rag。

fast_rag 和 research_rag 当前都进入同一个 rag 节点，区别主要保存在 route 和 trace 中。它为后续差异化 top_k、图检索深度、模型预算预留了接口。

**常见问题：为什么不用 LLM 做路由？**

- 规则路由延迟低、零模型成本、结果稳定。
- 新生高频意图有限，规则足以建立可靠基线。
- LLM 路由更灵活，但会引入额外调用、不可重复性和路由幻觉。
- 后续可以把规则结果作为先验，只有低置信度时再调用小模型分类。

### 5.5 direct 和 clarify

direct 只处理问候和能力介绍，不检索、不调用模型，置信度为 1。

clarify 用于空问题或信息量太少的问题，返回具体补充方向。RAG 内部还有第二层澄清：即使路由进入 rag，如果证据质量不足，也会返回按意图定制的补充建议。

### 5.6 rag 节点

rag 节点调用 NewStudentAssistant.ask，得到 AnswerResult，并将以下信息写入 trace：

- route。
- source_count。
- confidence。
- 是否触发 corrective retrieval。
- generation_mode 是 llm、extractive 还是 clarification。

### 5.7 verify 节点

verify 是最终门：

1. 检查答案中的 [S1] 形式引用。
2. 有来源但模型没有引用时，补充参考来源。
3. direct 路由直接视为 grounded。
4. RAG 路由必须同时满足证据充分、引用编号能映射到 sources、来源为 active official/maintained 且不要求澄清，才视为 grounded。
5. 未 grounded 时追加“以学校官方最新通知为准”的警告。

grounded 不是“答案一定正确”的同义词。它表示答案至少绑定了当前检索证据。事实是否正确还取决于知识源是否有效、检索是否命中和答案是否忠实。

### 5.8 Agent 元数据如何落库

AgentOutcome.message_metadata 会保存：

~~~json
{
  "agent": {
    "framework": "langgraph",
    "graph_version": "phase2.1",
    "route": "fast_rag",
    "intent": "identity",
    "confidence": 0.82,
    "grounded": true,
    "need_clarification": false,
    "trace": []
  },
  "sources": [],
  "warnings": [],
  "retrieval": {}
}
~~~

这使历史消息不只是文本，还能回答：

- 当时为什么走这条路？
- 检索了哪些来源？
- 是否触发纠错检索？
- 模型是否降级？
- 安全过滤是否修改过输出？

## 6. 知识加载、切分与缓存

### 6.1 支持格式

loaders.py 支持：

- .qa 与 .json：识别 conversations 格式和 Q/A 字段格式。
- .md 与 .txt：按段落切分。
- .csv：每一行生成一个 KnowledgeChunk。
- .docx：直接读取 ZIP 中的 word/document.xml，不依赖 Word。
- .pdf：可选使用 PyMuPDF 提取每页文本。

### 6.2 KnowledgeChunk

一个知识块包含：

- id：基于来源和内容的稳定哈希。
- content：可检索正文。
- source：原始文件路径。
- title：标题。
- metadata：question、answer、category、page、urls、kind 等。

SearchHit 在此基础上增加 score、rank 和 signals，并可转换成前端需要的 S1、S2 来源对象。

### 6.3 文本切分

默认 size=700、overlap=120：

1. 先按空行分段。
2. 段落超过 700 字时滑窗切分。
3. 普通段落尽量合并到 700 字以内。
4. 相邻块保留 120 字重叠。

重叠用于减少关键句被边界截断的问题。代价是索引体积和相似块数量增加，因此后续还要做来源去重和多样性惩罚。

### 6.4 索引缓存

storage.py根据文件绝对路径、大小和mtime_ns生成24位fingerprint。fingerprint未变化时直接读取index.json；发生变化则重新加载和切分。这一层仍用于本地BM25、n-gram和GraphRAG构建。

pgvector链路额外把文档、Chunk和Embedding持久化到PostgreSQL，通过content_hash只生成新增或变化内容的向量。两套索引使用同一个稳定Chunk ID对齐：本地索引负责低成本词法基线，数据库索引负责Dense召回和跨Worker共享。

## 7. 基础混合检索

核心代码位于 retrieval.py。

### 7.1 BM25

BM25 兼顾词频、逆文档频率和文档长度。简化公式：

$$
score(D,Q)=\sum_{t\in Q} IDF(t)\cdot
\frac{tf(t,D)(k_1+1)}
{tf(t,D)+k_1(1-b+b\cdot |D|/avgdl)}
$$

项目使用 k1=1.5、b=0.75。

注释：

- tf：词在文档中出现多少次。
- IDF：词越少见，区分能力越强。
- 文档长度归一化：避免长文因为词多而天然占优。

### 7.2 字符 n-gram 检索

中文没有天然空格。tokenize 会同时产生：

- 单个汉字。
- 连续二字片段。
- 长文本中的连续三字片段。
- 英文和数字 token。

NgramIndex 用 Counter 表示查询和文档，并计算余弦相似度：

$$
cos(q,d)=\frac{q\cdot d}{||q||\,||d||}
$$

它不是神经网络向量，而是字符统计向量。优势是零模型依赖、中文口语变化有一定鲁棒性；缺点是无法真正理解跨词语义。

### 7.3 RRF 融合

BM25 和 n-gram 的原始分数尺度不同，直接相加不稳定。RRF 只利用名次：

$$
RRF(d)=\sum_r \frac{1}{k+rank_r(d)}
$$

项目 k=60。某文档在多个检索器中排名靠前，融合分数就更高。

### 7.4 pgvector Dense检索

Worker对经过会话上下文改写的最终查询调用一次`text-embedding-v4`，得到1024维向量，再使用pgvector余弦距离从active Chunk中取Top 40。知识向量使用HNSW索引，并按`embedding_model`和`embedding_version`过滤。

三种运行模式：

- `off`：完全保持原词法链路；
- `shadow`：真实执行Dense召回但不改变答案，只记录候选和延迟；
- `hybrid`：Dense候选进入Weighted RRF和最终重排。

Embedding或数据库查询异常会记录`fallback`，然后继续使用BM25/n-gram，不让外部Embedding服务成为回答单点。

### 7.5 领域重排

基础重排加入：

- 查询 token 覆盖率。
- 完整查询是否出现在文档中。
- 标题或原问题匹配。
- 新生领域词加权。
- 办理动作词加权。
- 总览问题对目录块加权。
- 类目路径加权。
- 结构化 QA 加权。

这是一种 feature-based reranking，不是训练出来的 cross-encoder。优点是可解释、低成本；缺点是权重需要人工调试。

## 8. Advanced RAG

核心代码位于 advanced.py。

### 8.1 Query Planner

系统先推断意图，再产生最多 9 个查询变体：

- 原问题。
- 意图扩展词。
- 场景路由词。
- 流程问题扩展“步骤、材料、注意事项”。
- 总览问题扩展“目录、清单、汇总”。
- 从问题中抽取的高信息量词。

例如“校园卡丢了怎么办”会被补充“校园卡、挂失、补办、充值、办理流程”等词。

### 8.2 多查询融合

每个查询变体分别检索。原始问题权重为 1.0，扩展问题权重为 0.88。候选文档积累：

- fusion_score。
- best_base_score。
- best_rank。
- query_hits。

多查询命中次数能反映证据是否在不同改写下保持稳定。

### 8.3 Advanced 最终分数

当前代码中的线性组合：

~~~text
final =
  0.62  * best_base_score
  + 2.35 * fusion_score
  + 0.20 * coverage
  + 0.10 * authority
  + 0.10 * direct_answer_fit
  + 0.07 * route_match
  + 0.05 * evidence_density
  + 0.05 * multi_query_support
  + 0.035 * freshness
  + 0.12 * dense_similarity
~~~

这些权重是工程启发式权重，不是监督学习得到的参数，因此不能称为“训练出的 reranker”。

### 8.4 来源权威度

source_authority 的默认分为 0.42，再根据来源调整：

- QA、PDF、普通文档、目录块分别加权。
- Documents、data、结构化 QA 有额外加权。
- QQ 来源轻微降权。
- 包含“手册、通知、办法、指南、培养方案”等词加权。
- “已失效、疫情期间”等内容降权。

这体现了业务风险意识：相似度高不等于来源可靠。

### 8.5 时效性

freshness_score 从来源、标题和前 500 字提取 20xx 年：

- 当前或上一年：+0.12。
- 两年内：+0.05。
- 四年内：-0.03。
- 更旧：-0.12。

它只能发现显式年份，不能替代正式有效期字段。生产知识库需要 effective_from、effective_to 和审核状态。

### 8.6 证据质量诊断

quality 综合：

- top_score。
- query coverage。
- authority。
- source diversity。
- multi-query support。
- route match。
- 第一名与第二名的 margin。
- freshness。
- semantic_support，即Top结果的Dense相似度支持。

证据充分条件：

~~~text
top_score >= 0.15
quality >= 0.28
coverage >= 0.06 或 semantic_support >= dense_min_similarity
~~~

诊断会记录 low_top_score、low_query_coverage、weak_source_authority、low_source_diversity、possibly_stale_evidence 等原因。

### 8.7 Corrective Retrieval

第一轮无结果、sufficient=false、quality<0.34 或结果少于 3 条时，系统追加：

- “南京大学 新生”。
- 完整意图扩展词。
- “常见问题 官方通知”。
- 场景路由词。

然后扩大候选池重搜。这是 CRAG/Self-RAG 思想的轻量工程实现：先评估证据，再决定是否纠错，而不是一次召回后直接生成。

### 8.8 多样性

候选选择时，同来源扣 0.055，同 category_path 再扣 0.025，总惩罚最多 0.16。目的是防止同一文件的相邻重叠块占满 Top-K。

## 9. 轻量 GraphRAG

核心代码位于 graph.py。

### 9.1 图里有什么

- GraphTerm：主题词节点。
- GraphCommunity：按目录或来源形成的社区。
- chunk_terms：知识块到主题词的映射。
- 共现边：同一个知识块中的主题两两连接。
- 主题到知识块、主题到社区、社区到知识块的加权映射。

### 9.2 图如何构建

每个知识块最多提取 12 个主题，前 8 个主题两两建立共现边。知识块根据类型和来源获得权重：

- PDF、document、QA、directory 加权不同。
- Documents 加权。
- QQ 降权。

社区不是运行社区发现算法得到的，而是根据 Documents、QQ、data 路径和 kind 进行可解释聚合。

### 9.3 图查询

查询过程：

1. 从问题抽取主题。
2. 匹配图中主题节点。
3. 沿共现邻居扩展，邻居权重按 0.42 衰减。
4. 聚合社区分数。
5. 将主题和社区分数传播到知识块。
6. 与 Advanced RAG 结果合并。

总览型问题包含“哪些、汇总、目录、清单”等词时，global_query_likelihood 较高，系统允许加入一个社区摘要块；具体办理问题仍以原始文档块为主。

### 9.4 为什么不用 Neo4j

当前知识规模和预算不需要额外图数据库。JSON 缓存有以下优势：

- 无额外服务和运维成本。
- 容易跟随知识 fingerprint 重建。
- 检索行为可重复、可测试。
- 适合 4 核 8 GiB ECS。

代价：

- 规则抽取能力有限。
- 社区划分依赖目录。
- 缺少复杂图查询和在线更新。
- 社区摘要是模板拼接，不是 LLM 审核后的语义摘要。

准确的说法是“GraphRAG 风格的轻量本地图检索层”，不能描述为“完整实现微软 GraphRAG”。

## 10. 生成、引用与安全降级

### 10.1 千问接入

OpenAICompatibleLLM 只使用 Python 标准库 urllib，发送：

- model=qwen-plus。
- temperature=0.2。
- max_tokens=900。
- OpenAI-compatible messages。
- 30 秒默认超时。

base_url 支持：

- 已经以 /chat/completions 结尾。
- 以 /v1 结尾。
- 普通根地址自动补 /v1/chat/completions。

### 10.2 Prompt 约束

系统提示要求：

- 只根据给定资料回答。
- 关键结论标 [S1]。
- 资料不足时拒答并说明下一步。
- 年份、金额、时间和入口提醒核验。
- 不输出证据中没有的网址、邮箱、电话、系统、公众号、部门和政策结论。

提示词是第一层约束，不是唯一安全措施，因为模型可能不完全遵守。

### 10.3 后处理安全层

系统从答案中提取 URL，与证据中的 URL 集合比较。还检查本科招生网、迎新系统、公众号、教务处、辅导员等高风险渠道词是否在证据中出现。

处理策略：

1. 如果无证据内容只出现在答案后缀，保留前面已经有引用的安全部分。
2. 如果无法得到带引用的安全前缀，放弃模型答案。
3. 回退到抽取式回答。
4. 在 diagnostics.generation 中记录 safety_filter 或 fallback_reason。

这种策略比“发现一个错误就丢掉整个答案”更注重可用性，同时保留审计记录。

### 10.4 抽取式降级

以下情况会降级：

- 未配置 LLM base_url。
- HTTP 错误。
- 网络错误。
- 返回 JSON 无效。
- 返回内容为空。
- 安全过滤无法保留可信前缀。

降级答案直接抽取前两条证据的 answer/content，并加 [S1]、[S2]。因此模型故障不会让整个问答系统不可用。

### 10.5 过期资料提醒

命中历史年份、旧年级、日期、金额或链接时，warnings 提醒用户核对最新通知。

注意：提醒不能修复过期事实。正式方案仍需知识有效期、版本优先级和审核流程。

## 11. PostgreSQL 数据模型与一致性

### 11.1 表设计

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| users | email、username、password_hash、is_active | 普通账号 |
| auth_sessions | token_hash、expires_at、revoked_at | 持久登录态 |
| audit_events | event_type、outcome、request_id、fingerprint | 安全审计 |
| conversations | owner_id、title、deleted_at | 用户对话 |
| messages | role、content、message_metadata | 用户与助手消息 |
| agent_runs | input/output message、status、idempotency_key、error | 异步任务 |

### 11.2 Run 状态机

~~~mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running: Worker 原子 claim
    queued --> failed: 入队失败
    queued --> cancelled: 用户取消
    running --> completed: 写入助手消息
    running --> failed: Worker 异常
    running --> cancelled: 用户取消
~~~

claim_queued 使用带条件的 SQL UPDATE：

~~~text
UPDATE agent_runs
SET status='running'
WHERE id=:id AND status='queued'
RETURNING id
~~~

即使同一个 run_id 被重复投递，也只有一个 Worker 能把 queued 改成 running。

### 11.3 幂等键

前端为每次发送生成 UUID 作为 Idempotency-Key。数据库在 conversation_id + idempotency_key 上建立唯一约束。

- 同 key、同内容：返回已有 Run。
- 同 key、不同内容：409 Conflict。
- 两个并发请求同时插入：一个成功，另一个捕获 IntegrityError 后查询已有 Run。

幂等解决的是“浏览器重试导致重复任务”，不是所有分布式事务问题。

### 11.4 所有权隔离

ConversationRepository 查询始终带：

~~~text
conversation.id = ?
owner_id = 当前用户
deleted_at IS NULL
~~~

Run 查询通过 join conversations 检查 owner_id。访问他人的资源返回 404 而不是 403，减少资源存在性泄露。

### 11.5 软删除

删除对话只设置 deleted_at。优点是审计和误删恢复空间更大；代价是需要定期清理策略，列表和详情查询必须始终过滤 deleted_at。

## 12. Redis 的四种用途

### 12.1 任务队列

API 使用 RPUSH，Worker 使用 BLPOP。队列中只存 run_id，完整内容仍在 PostgreSQL。

### 12.2 事件流

每个 Run 对应一个 Redis Stream：

~~~text
agentic_rag:runs:events:{run_id}
~~~

事件包括：

- run.status。
- agent.step。
- message.delta。
- message.completed。
- run.failed。
- run.cancelled。
- heartbeat 由 API 在 XREAD 超时后产生。

Stream 最多近似保留 1000 条，TTL 默认 86400 秒。

### 12.3 取消标记

取消使用带 TTL 的 Redis key。Worker 在调用 Agent 前和每次推送 delta 前检查。取消不能中断已经在进行的同步千问 HTTP 请求，只能阻止后续写入和推送。

### 12.4 原子限流

Lua 脚本在 Redis 内完成 INCR、首次 EXPIRE 和 TTL 读取，避免应用层“先读后写”的竞态。

认证同时按：

- client fingerprint。
- HMAC 后的账号标识。

提问同时按：

- user id。
- client fingerprint。

### 12.5 为什么 Redis 客户端禁用 socket_timeout

redis-py 默认 socket_timeout 约 5 秒，而 BLPOP 最长阻塞 5 秒、XREAD 最长阻塞 15 秒。客户端超时与服务端正常阻塞竞争，会把健康的空闲 Worker 误判成失败。因此连接只设置 connect timeout，不设置普通 socket timeout。

## 13. SSE 与前端恢复

### 13.1 为什么选 SSE

当前通信主要是服务器单向推送：

- 用户提交问题走普通 POST。
- 回答、状态和错误由服务器推给浏览器。

SSE 相比 WebSocket：

- 基于 HTTP，Caddy 和浏览器支持简单。
- EventSource 自动重连。
- 文本事件格式易调试。
- 支持 Last-Event-ID 恢复。

如果未来需要语音、双向实时协作或客户端高频控制，再考虑 WebSocket。

### 13.2 SSE 格式

每条事件包含：

~~~text
id: 1720000000000-0
event: message.delta
data: {"text":"..."}
~~~

API 开始时发送 retry: 3000。Redis Stream ID 作为 SSE id，浏览器可用 Last-Event-ID 从游标后继续读取。

### 13.3 前端如何展示

发送消息后：

1. 前端把后端返回的 user message 放入列表。
2. 创建 streaming-{run_id} 临时助手消息。
3. message.delta 到达时追加 content。
4. message.completed 到达时，用数据库中的正式消息替换临时消息。
5. failed 或 cancelled 时删除临时消息。

### 13.4 刷新恢复

ConversationDetail 返回 active_run。页面刷新后：

- 从 PostgreSQL 读取历史消息。
- 如果存在 queued/running Run，重新连接 SSE。
- Redis Stream 重放尚在 TTL 内的事件。
- 最终 message.completed 与数据库正式消息对齐。

### 13.5 “流式”实现的真实边界

Worker 当前调用 runtime.invoke，内部使用 llm.chat(stream=false)。模型完整返回后，Worker 每 14 个字符发布一个 message.delta，并等待 25 ms。

因此：

- SSE 通道是真实流式、可重放、可恢复的。
- UI 确实逐步显示文本。
- 但模型首 token 延迟没有被隐藏，当前不是模型原生 token streaming。

改进方式是让 Worker 使用 stream_chat，收到模型 delta 后立即做增量安全处理并发布。难点是安全过滤目前依赖完整答案，真流式需要缓冲高风险片段或采用“草稿区 + 完成后确认”的策略。

## 14. 账号与安全

### 14.1 密码

pwdlib 的 recommended PasswordHash 当前使用 Argon2。哈希操作通过 asyncio.to_thread 执行，避免直接阻塞事件循环。

验证不存在账号时仍校验一个 dummy hash，减少“存在账号”和“不存在账号”在计算路径上的时间差。

### 14.2 会话 Token

- 使用 secrets.token_urlsafe(32) 生成随机 Token。
- 浏览器只通过 HttpOnly Cookie 持有原始 Token。
- 数据库只保存 SHA-256 token_hash。
- Cookie 设置 SameSite=Lax。
- 生产配置强制 Secure=true。
- 登出设置 revoked_at 并删除 Cookie。

即使数据库泄露，攻击者不能直接拿 token_hash 当 Cookie 使用。

### 14.3 指纹与隐私

client fingerprint 由 IP 和 User-Agent 通过 HMAC-SHA256 得到。账号标识限流也使用 HMAC，不把邮箱或用户名直接写入 Redis key。

HMAC 与普通 SHA-256 的区别：HMAC 需要服务器密钥，攻击者不能仅凭常见邮箱字典离线还原 key。

### 14.4 生产配置校验

environment=production 时：

- session_cookie_secure 必须为 true。
- audit_hash_key 不能使用开发默认值。

这是 fail fast：配置不安全时服务拒绝启动。

### 14.5 网关安全头

Caddy 添加：

- X-Content-Type-Options: nosniff。
- X-Frame-Options: DENY。
- Referrer-Policy: strict-origin-when-cross-origin。
- Permissions-Policy 禁止摄像头、麦克风、定位。
- 移除 Server。

### 14.6 仍需改进

- 普通账号没有邮箱验证、找回密码、MFA 和学校统一身份认证。
- Staging 使用 HTTP，因此 Cookie Secure=false，只适合受限测试环境。
- trust_proxy_headers 只能在可信反向代理后开启，否则客户端可伪造 X-Forwarded-For。
- 尚无独立 CSRF token，当前主要依赖 SameSite=Lax 和同源架构。
- 知识内容仍应视为不可信输入，需要更系统的 prompt injection 隔离。

## 15. 前端产品实现

### 15.1 会话门

AppGate 启动时调用 /auth/me：

- 200：进入 ChatShell。
- 401：显示 AuthScreen。
- 全局监听 agentic-rag:unauthorized，任何 API 401 都能清空登录态。

### 15.2 ChatShell

已实现：

- 创建新对话。
- 历史列表。
- 中文标题搜索。
- 对话切换。
- 重命名。
- 删除确认。
- 自动生成首条消息标题。
- 底部居中输入框。
- Enter 发送、Shift+Enter 换行。
- 推荐问题。
- 增量回答。
- 停止生成。
- 移动端侧栏。
- 浅色/深色主题。
- 回答来源和元数据恢复。

### 15.3 HTTP 环境下的幂等 UUID

crypto.randomUUID 在非安全上下文 HTTP 中可能不可用。前端先尝试 randomUUID；不可用时使用 crypto.getRandomValues 自行设置 UUID v4 的 version 和 variant 位。

这解决了 ECS 暂无域名和 HTTPS 时浏览器发送消息报错的问题，同时没有退化到 Math.random。

## 16. Docker 与 ECS 部署

### 16.1 Compose 服务

基础 Compose 包含：

- postgres。
- redis。
- migrate。
- api。
- worker。
- web。

Staging 叠加 caddy，生产叠加自动 HTTPS 所需端口和 Caddy 持久卷。

### 16.2 migrate 一次性容器

migrate 运行 alembic upgrade head。API 和 Worker 依赖 migrate 成功后才启动。

这样数据库结构升级成为部署流程的一部分，避免应用新版本连接旧表结构。

### 16.3 后端镜像

- Python 3.12 slim。
- 安装项目包。
- 拷贝知识 fixture 和迁移文件。
- 创建 uid=10001 的非 root 用户。
- API 与 Worker 复用同一镜像，不同 command。

### 16.4 前端镜像

三阶段构建：

1. deps 安装 pnpm 依赖。
2. builder 生成 Next.js standalone。
3. runtime 只复制运行产物，以非 root 用户启动。

### 16.5 Caddy 同源代理

- /api、/health、/docs 转发到 FastAPI。
- 其他请求转发到 Next.js。
- staging 监听 80。
- production 使用 SITE_ADDRESS 自动申请 TLS。
- flush_interval=-1 避免代理缓存 SSE。

### 16.6 中国大陆网络适配

ECS 访问 Docker Hub 不稳定。Staging 使用 Amazon ECR Public 的 Docker Official Images，并使用阿里云 PyPI 镜像构建 Python 依赖。k6 改为优先使用宿主机 APT 安装版本。

### 16.7 当前部署资源

- 阿里云杭州地域。
- Ubuntu 24.04。
- x86_64。
- 4 核、约 8 GiB 内存。
- 40 GiB 系统盘。
- 2 GiB Swap。

ECS 只运行应用、检索和数据服务；大模型推理由百炼承担。

## 17. 可观测性与故障恢复

### 17.1 请求可观测

API 中间件：

- 接受合法 X-Request-ID，否则生成 UUID。
- 响应回传 X-Request-ID。
- 添加 Server-Timing: app;dur=...。

request_id 同时进入审计事件，可串联 HTTP 请求和业务动作。

### 17.2 OpenTelemetry

配置 AGENTIC_RAG_OTEL_ENABLED 后，FastAPIInstrumentor 将 Trace 导出到 OTLP HTTP endpoint。当前 Compose 未默认部署 Collector，属于已接入代码、未默认启用的能力。

### 17.3 存活与就绪

- /health/live：只判断进程可响应，不访问依赖。
- /health/ready：并发 ping PostgreSQL 和 Redis，任何依赖异常返回 503。

区分二者的原因：依赖故障时进程仍然活着，不应无限重启；但负载均衡器不应继续把业务流量发给未就绪实例。

### 17.4 数据与服务恢复

- PostgreSQL 和 Redis 使用命名卷。
- Redis 开启 AOF 和 noeviction。
- 容器 restart: unless-stopped。
- resilience 脚本验证 Worker 停止后恢复、API/Web 重启持久性和 PostgreSQL 备份恢复。

## 18. 失败场景与系统行为

| 故障 | 当前行为 | 后续改进 |
| --- | --- | --- |
| 千问超时或网络错误 | 抽取式回答，记录 fallback_reason | 重试、熔断、模型路由 |
| Redis 入队失败 | Run 标 failed，API 返回 503 | Outbox Pattern |
| Redis 短暂读取失败 | Worker 记录异常，等待后重试 | 指标告警 |
| Worker 停止但任务仍在 List | 重启后继续 BLPOP | 多副本与健康监控 |
| Worker 在 BLPOP 后立刻崩溃 | 任务可能丢失或卡住 | Redis Stream Consumer Group、ACK、visibility timeout |
| 同一请求被重试 | 幂等键返回已有 Run | 客户端保留 key 到最终确认 |
| 两个 Worker 重复拿到同一 Run | claim_queued 只允许一个成功 | 保留数据库条件更新 |
| SSE 断开 | 可重新打开会话，使用 active_run 和 Stream 重放 | 前端显式保存 Last-Event-ID |
| 用户刷新 | 历史消息从 PostgreSQL 恢复 | 无 |
| 用户取消 | DB 标 cancelled，Redis 发取消事件 | 可取消上游 HTTP 请求 |
| 证据不足 | 澄清或拒答 | 知识缺口工单 |
| 模型补充无依据入口 | 安全截断或抽取式降级 | 结构化生成与事实判定器 |
| 旧知识命中 | 降权并提醒 | 正式有效期与版本发布 |

### 18.1 Outbox Pattern 注释

当前“数据库提交”和“Redis 入队”不是同一个事务。Outbox Pattern 会把待发送任务和业务数据放在同一数据库事务里，再由独立发布器可靠投递到 Redis。这样可以解决“数据库已提交，但进程在入队前崩溃”的窗口。

## 19. 测试与 CI

### 19.1 单元测试

覆盖：

- LangGraph 路由、RAG 路由、trace 和 JSON-safe metadata。
- 低质量证据不能标 grounded。
- URL、官方渠道和安全后缀过滤。
- 生产安全配置。
- Argon2 与不透明 Session。
- Lua 限流结果。
- Redis blocking timeout 配置。
- Worker Redis 短暂故障恢复。
- Pydantic 输入边界。
- SSE 格式。

### 19.2 集成测试

GitHub Actions 启动真实 PostgreSQL 17 和 Redis 8，执行：

- Alembic 迁移。
- 注册和 Cookie。
- 对话创建。
- 同 key 幂等重放。
- 同 key 不同内容冲突。
- 跨用户资源隔离。
- Redis 出队。
- Worker 处理。
- Agent step、delta 和 completed 事件。
- 消息和 metadata 落库。
- 登出失效。

### 19.3 远程 E2E

对真实 ECS 入口验证：

- 安全头。
- live/ready。
- 登录与会话持久化。
- CRUD。
- SSE。
- 取消。
- 重登后数据仍存在。

### 19.4 GitHub Actions 分层

- repository-guard：拒绝本地知识、密钥、索引和旧项目名。
- backend：ruff、compileall、mypy、pytest。
- integration：真实 PostgreSQL/Redis。
- frontend：pnpm install、eslint、tsc、Next build。
- compose：Compose、ECS Bash、镜像引用和 k6 inspect。

## 20. 效果评测与容量结果

### 20.1 Agent fixture 效果门禁

当前小型真实模型门禁包含：

- 问候直答。
- 测试通知真实性。
- 原始知识不入仓原因。
- 领域外问题拒答。

检查：

- route。
- grounded。
- citation。
- URL grounding。
- channel grounding。
- generation_mode 是否为 llm。
- 总体与 LLM P50/P95。

当前四例全部通过。这个样本只能验证安全链路，不代表正式学校知识库的整体准确率。

### 20.2 V1 RAG 消融

历史 14 题评测对比 no_rag、BM25、hybrid、advanced 和 graphrag，并生成 JSON/HTML 柱状图。历史结果中 full_kb_hybrid、advanced、graphrag 的 Top-3 来源命中达到 100%。

注意：这组数据基于本地历史知识库实验，不应和当前脱敏 fixture 的线上效果混为一谈。

### 20.3 真实模型容量

| 档位 | 成功率 | HTTP 失败 | 问答 P95 |
| --- | ---: | ---: | ---: |
| 2 并发，4 次 | 100% | 0% | 8.04 s |
| 5 并发，5 次 | 100% | 0% | 17.27 s |

5 并发 HTTP P95 约 1.23 s，而问答 P95 为 17.27 s，说明瓶颈主要是单 Worker 排队和云模型生成。

### 20.4 平台容量

20 并发正式门禁：

- 180/180 检查成功。
- 0 HTTP 失败。
- 认证 P95 4.30 s。
- 读取 P95 0.93 s。
- 写入 P95 0.32 s。
- 完整旅程 P95 6.07 s。

100 用户突发：

- 900/900 检查成功。
- 0 HTTP 失败。
- 完整旅程 P95 27.32 s。
- 认证 P95 20.61 s，超过 20 s 门槛。
- 读取 P95 6.37 s，超过 3 s 门槛。

原因推断：100 个用户同时注册触发大量 Argon2 哈希，4 核 CPU 饱和，认证和读取共同排队。不要为了压测结果降低密码哈希强度。若业务真有集中注册，可提前导入账号、限制注册并发或增加 API 副本。

### 20.5 P95 注释

P95=6 秒表示 95% 请求不超过 6 秒，最慢的 5% 可能更久。P95 比平均值更能反映尾延迟，但小样本模型测试中的 P95 稳定性有限。

## 21. 关键技术取舍

### 21.1 为什么用 LangGraph

- 显式状态和条件边。
- 可插入验证节点。
- trace 容易持久化。
- 后续可加 checkpoint 和 human-in-the-loop。

代价是当前图比较固定，创新点主要在“有界可审计”，不是自主规划能力。

### 21.2 为什么不用本地 vLLM

4 核 8 GiB CPU ECS 无法经济运行大模型。使用百炼：

- 没有 GPU 固定成本。
- 模型升级和并发由云端负责。
- 项目专注 Agent、RAG 和工程链路。

代价是网络延迟、按量费用、供应商配额和外部服务依赖。

### 21.3 为什么 PostgreSQL 和 Redis 都要

- PostgreSQL：持久、事务、关系约束、审计。
- Redis：队列、Stream、短 TTL 状态、原子计数。

只用 PostgreSQL 可以做队列，但轮询和事件流实现更复杂；只用 Redis 又不适合作为完整业务事实数据库。

### 21.4 为什么选择pgvector而不是独立向量数据库

项目已经保留零模型依赖的词法基线，并在同一个PostgreSQL 17中加入pgvector：

- 复用现有事务、备份、连接和监控体系；
- 4核8 GiB ECS不需要再运行一个常驻向量服务；
- 文档治理元数据、Chunk和向量可以通过外键保持一致；
- `off/shadow/hybrid`允许先观测再影响答案；
- BM25和n-gram仍是向量检索的消融基线与故障降级路径。

代价是超大规模向量检索和原生dense/sparse多向量能力不如Qdrant或Milvus。当前知识规模和100注册用户目标下，低运维成本更重要；当知识块达到数十万并与业务SQL产生明显资源竞争时，再评估拆分。

### 21.5 为什么用 SSE

回答是服务器单向事件，SSE 足够简单；WebSocket 的双向能力当前没有被充分利用。

### 21.6 为什么保留抽取式降级

它把 LLM 从单点依赖变成可选增强，并为低成本测试提供确定性输出。缺点是语言组织不自然，可能直接暴露文档格式。

## 22. 常见技术问题与参考回答

### Q1：请用一分钟介绍项目

> 这是一个面向高校新生事务的 Agentic RAG 系统。我没有把模型调用直接塞进 HTTP 请求，而是把用户消息和 Agent Run 先持久化到 PostgreSQL，通过 Redis 队列交给独立 Worker。Worker 使用 LangGraph 在直接回答、澄清、快速检索和研究型检索之间路由，再用 BM25、字符n-gram、pgvector Dense、RRF、领域重排和轻量GraphRAG找证据，最后调用阿里云千问生成带引用答案。Dense链路支持off、shadow和hybrid灰度，失败自动回退词法检索。事件通过Redis Stream和SSE推给Next.js，刷新后可以从数据库和活动Run恢复。系统已用Docker Compose和Caddy部署到阿里云ECS；pgvector代码已完成，但ECS镜像迁移和真实Embedding评测仍需按上线手册执行。

### Q2：你的 Agent 和普通 RAG 有什么区别

> 普通 RAG 通常固定执行“检索再生成”。这里先根据问题状态选择 direct、clarify、fast_rag 或 research_rag，检索后还经过 verify。证据不足时会纠错检索或澄清，而不是强行生成。每个节点轨迹、路由、证据和生成模式都会落库，所以它是一个有界、可审计的 Agent workflow。

### Q3：为什么不用 ReAct

> 校务问答的工具集合有限，而且错误答案有办事风险。自由 ReAct 容易产生不可控的循环、成本和工具调用。固定图更适合当前阶段。未来如果加入多个官方系统查询工具，可以在 research 分支内部增加受限 ReAct 子图。

### Q4：fast_rag 和 research_rag 现在真的不同吗

> 当前两者都进入同一检索节点，区别保存在路由和轨迹里。现阶段先验证路由和工程链路，后续会让 research_rag 使用更高 top_k、更强图扩展或多轮工具查询。对外说明时不能把预留接口说成已完成差异化执行。

### Q5：你的混合检索如何融合向量和关键词

> 词法侧保留BM25和字符n-gram，语义侧使用text-embedding-v4生成1024维查询向量，在pgvector中做HNSW余弦召回。不同检索器的原始分数不在同一尺度，所以先按排名做Weighted RRF，再叠加权威度、时效性、直接回答匹配和有界语义支持度。上线采用off、shadow、hybrid三段式，Embedding或pgvector失败时自动只走词法链路。

### Q6：RRF 为什么适合这里

> BM25 和 n-gram 分数不在同一尺度，直接相加需要归一化。RRF 只使用名次，不依赖原始分数尺度，而且能奖励在多个检索器中都靠前的文档。

### Q7：GraphRAG 如何实现

> 系统从知识块提取领域主题，按同块共现建边，按目录和来源形成社区，并生成模板化社区摘要。查询时匹配主题、扩展邻居、计算社区和知识块加权，再与 Advanced RAG 合并。它是轻量本地图层，不依赖 Neo4j，也不是完整微软 GraphRAG。

### Q8：如何判断证据不足

> 综合最高分、查询覆盖率、来源权威度、来源多样性、多查询支持、排序 margin 和时效性得到 quality。低于阈值时执行纠错检索，仍不足就澄清或拒答。confidence 是检索派生信号，不是事实正确率。

### Q9：如何防止模型幻觉

> 四层：受限 Prompt、证据质量门禁、引用与 grounded 检查、答案后处理。后处理会拒绝证据中没有的 URL 和高风险官方渠道；可以安全截断时保留带引用前缀，否则回退抽取式答案。

### Q10：为什么需要独立 Worker

> 模型调用慢且波动大。独立 Worker 让 API 快速返回 202，避免占住请求线程；任务状态可以持久化；Worker 和 API 可以独立扩容；失败可以记录为 Run 状态。

### Q11：如何保证消息不重复

> 客户端发送 Idempotency-Key，数据库对 conversation_id + key 建唯一约束。同 key 同内容返回原 Run，同 key 不同内容返回 409。Worker 再通过原子 claim_queued 防止重复消费。

### Q12：Redis 队列可靠吗

> 当前是 RPUSH/BLPOP，满足演示规模，但不是严格可靠队列。BLPOP 后 Worker 崩溃存在丢失窗口。生产升级会采用 Redis Streams Consumer Group、ACK 和 pending reclaim，或 Celery/RQ；数据库 Outbox 解决提交与入队原子性。

### Q13：SSE 断线如何恢复

> Redis Stream 保存带 ID 的事件，SSE 支持 Last-Event-ID；PostgreSQL 保存最终消息，ConversationDetail 返回 active_run。刷新后重新读取历史并订阅活动 Run。当前前端主要通过重新打开会话恢复，还可以进一步显式持久化最后游标。

### Q14：你这是真流式吗

> 传输层是真实 SSE，事件可重放；但生产 Worker 当前等待模型完整回答后再按字符分片，所以不是模型原生 token 流。后续改成 stream_chat 时要同时解决增量内容安全审核。

### Q15：如何做账号安全

> Argon2 哈希密码；随机 Session Token 只通过 HttpOnly Cookie 返回，数据库只存 SHA-256；生产强制 Secure Cookie；SameSite=Lax；登录按客户端和账号双限流；账号标识通过 HMAC 隐私化；资源查询绑定 owner_id。

### Q16：为什么100并发认证很慢

> 100个并发用户同时执行 Argon2，这是 CPU 和内存密集操作。4核机器出现资源争用，认证 P95 约20.61秒，并拖慢读取。正常目标是100注册用户、10至20人峰值在线，20并发门禁全部通过。

### Q17：系统瓶颈在哪里

> 平台接口20并发旅程 P95 约6.07秒；真实模型5并发问答 P95约17.27秒，HTTP P95远低于问答P95。当前主要瓶颈是单 Worker串行处理和云模型生成，而不是基础HTTP链路。

### Q18：如何扩容

> API可以多副本，因为状态在PostgreSQL和Redis；Worker可以增加副本，但应先升级可靠队列和确认机制。还要考虑百炼RPM/TPM配额、数据库连接池、Redis吞吐和成本。Caddy前面可再接云负载均衡。

### Q19：知识库如何更新

> 当前正式目录保存经官方页面核验的结构化摘要。加载器解析 front matter 并排除 README/fixture；staging 发布要求 active、official/maintained、南京大学 HTTPS 来源及最小文档/Chunk 数。入库按 content_hash 增量生成向量并事务发布，Dense SQL 和 Agent verify 都会过滤未发布来源。自动采集、可视化差异、审批和一键回滚后台仍未实现。

### Q20：项目最大的不足

> 三点：正式知识目前仍是人工维护的首批覆盖，若没有持续更新会快速产生缺口；当前没有学习型 reranker；任务队列缺少 ACK 和 visibility timeout。另外模型原生 token 流、正式监控告警和知识审核后台也需要继续完善。

### Q21：如果重新设计你会先改什么

> 先用新的正式语料重建向量并跑覆盖率/安全评测，按失败清单补 P0 知识；随后升级任务可靠性和知识审批回滚链路；只有消融证明收益后才加入 qwen3-rerank，最后做模型原生流式和多 Worker 扩容。

## 23. 常见术语速查

| 术语 | 简要解释 |
| --- | --- |
| Agent | 根据状态选择动作路径的系统，不等同于无限自主循环 |
| LangGraph | 用状态图组织Agent节点和条件边的框架 |
| RAG | 先检索外部证据，再让模型基于证据生成 |
| BM25 | 基于词频、逆文档频率和文档长度的稀疏检索 |
| n-gram | 连续n个字符组成的片段，用于中文近似匹配 |
| RRF | 按多个排序结果中的名次融合候选 |
| rerank | 对召回候选进行第二阶段排序 |
| grounded | 回答绑定了当前检索证据和引用 |
| GraphRAG | 利用实体、关系或社区图辅助检索与全局回答 |
| 幂等 | 同一请求重复执行，不产生额外副作用 |
| SSE | 基于HTTP的服务器单向事件推送 |
| Redis Stream | 带持久ID的追加事件日志 |
| P95 | 95%样本不超过的延迟值 |
| Argon2 | 面向密码存储的内存困难哈希算法 |
| HMAC | 使用密钥的消息认证码，可用于隐私化标识 |
| TTL | 数据自动过期时间 |
| AOF | Redis将写命令追加到文件的持久化方式 |
| Alembic | SQLAlchemy生态的数据库迁移工具 |
| liveness | 进程是否活着 |
| readiness | 服务依赖是否就绪、能否接流量 |
| OpenTelemetry | 统一采集Trace、Metric和Log的标准 |
| Outbox Pattern | 用数据库事务记录待投递事件，再异步可靠发布 |

## 24. 三种项目介绍长度

### 24.1 30秒

> 我做了一个面向高校新生事务的Agentic RAG系统。它用LangGraph做问题路由和证据验证，用Advanced RAG与轻量GraphRAG检索资料，再调用阿里云千问生成带引用答案。工程上采用FastAPI、PostgreSQL、Redis Worker、SSE和Next.js，并通过Docker Compose和Caddy部署到阿里云ECS。

### 24.2 2分钟

> 项目最初是比赛问答原型，后来我把它重构成可部署系统。用户消息先写入PostgreSQL并创建Agent Run，API通过Redis入队后立即返回202，独立Worker再执行LangGraph。图里有直接回答、澄清、RAG和验证节点。检索侧使用BM25、字符n-gram、RRF、多查询融合、证据质量诊断、纠错检索和轻量GraphRAG。千问失败或输出越界时会回退到带引用的抽取式回答。前端用Next.js和SSE展示回答，支持历史会话、刷新恢复和停止生成。系统已经部署到阿里云ECS，并做了账号隔离、限流、CI、故障恢复和20并发容量门禁。

### 24.3 5分钟展开顺序

1. 场景风险：校务信息分散且易过期。
2. 总体架构：Caddy、Web、API、PG、Redis、Worker、Qwen。
3. Agent：有界路由和verify。
4. RAG：混合召回、RRF、Advanced、Graph。
5. 可靠性：Run、幂等、SSE恢复、降级。
6. 安全：Argon2、Session、owner隔离、限流和输出过滤。
7. 部署与测试：ECS、Docker Compose、CI和容量结果。
8. 局限与下一步：正式知识采集与审核后台、ECS Dense Shadow 验证、可靠队列、模型原生真流式。

## 25. 代码阅读路线

建议按以下顺序阅读：

1. README.md：项目边界。
2. src/agentic_rag/agent/runtime.py：LangGraph主图。
3. src/agentic_rag_v1/service.py：RAG问答与安全降级。
4. src/agentic_rag_v1/retrieval.py：BM25、n-gram和RRF。
5. src/agentic_rag_v1/advanced.py：多查询、评分和纠错。
6. src/agentic_rag_v1/graph.py：轻量图构建与融合。
7. src/agentic_rag/api/routes/conversations.py：消息受理与入队。
8. src/agentic_rag/worker.py：Worker处理与事件发布。
9. src/agentic_rag/broker.py：Redis队列、Stream和限流。
10. src/agentic_rag/repository.py 与 models.py：数据一致性。
11. apps/web/components/chat-shell.tsx：前端恢复和流式展示。
12. deploy/compose 与 deploy/caddy：部署拓扑。
13. tests：从断言反推系统契约。
14. docs/PHASE2_CAPACITY_BASELINE.md：实测边界。

## 26. 最终自检清单

讲解项目前确认自己能回答：

- 能否在白板上画出消息从浏览器到Worker再返回的链路？
- 能否解释为什么PostgreSQL是事实源、Redis是协调层？
- 能否写出RRF公式并解释为什么不直接相加？
- 能否说明BM25、n-gram、pgvector Dense和Weighted RRF如何配合？
- 能否说明当前GraphRAG和完整GraphRAG的差异？
- 能否解释grounded不等于绝对正确？
- 能否解释幂等键和数据库唯一约束如何配合？
- 能否解释SSE恢复以及当前不是真正token流？
- 能否指出BLPOP队列的可靠性缺口和改进方案？
- 能否解释100用户压力测试为什么认证变慢？
- 能否说清正式知识的来源、发布门禁、当前覆盖和仍然缺失的场景？
- 能否给出下一阶段最优先的三项改进？

如果这些问题都能脱稿回答，就能完整解释项目的中后台、AI 应用工程和 Agent/RAG 技术取舍。
