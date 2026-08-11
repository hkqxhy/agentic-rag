# pgvector 语义检索实施与上线手册

## 1. 当前状态

代码已经具备以下能力：

- PostgreSQL 17 + pgvector 0.8.6；
- `text-embedding-v4`、1024 维向量和余弦检索；
- `knowledge_documents`、`knowledge_chunks`、`knowledge_embeddings` 三层数据模型；
- 基于 `content_hash` 的增量向量生成和幂等发布；
- BM25、字符 n-gram、Dense 与 GraphRAG 的融合；
- `off`、`shadow`、`hybrid` 三段式发布；
- Embedding或向量查询失败时自动回退词法检索；
- Dense延迟、候选数、最高相似度、模型版本和降级原因诊断；
- lexical/dense/hybrid三组检索消融评测命令。

本文件描述的是已经进入仓库的实现。ECS仍需要先备份数据库、切换pgvector镜像、执行迁移并生成向量，才能打开Shadow或Hybrid模式。

## 2. 运行链路

```text
Worker取得Run
  -> 根据历史会话生成检索查询
  -> text-embedding-v4生成一次查询向量
  -> pgvector检索Top 40
  -> Shadow：只记录Dense结果，不影响回答
     Hybrid：把Dense候选传给Advanced/Graph RAG
  -> BM25/n-gram多查询召回
  -> Weighted RRF + 领域特征 + 语义支持度
  -> GraphRAG扩展
  -> Qwen生成或抽取式降级
  -> 持久化检索诊断、来源和Agent轨迹
```

Embedding调用和pgvector查询发生在Worker的异步上下文。现有LangGraph和本地RAG仍在线程中执行，因此不会阻塞FastAPI事件循环，也不需要增加第二套同步数据库连接池。

## 3. 数据表

迁移 `20260811_0003` 创建：

- `knowledge_documents`：文档来源、权威度、状态、checksum和治理元数据；
- `knowledge_chunks`：稳定Chunk ID、内容、来源、元数据和内容哈希；
- `knowledge_embeddings`：模型、版本、1024维向量和HNSW余弦索引。

向量和Chunk分表的原因：

1. 文本治理与Embedding生命周期不同；
2. 内容未改变时可以复用向量；
3. 可以保留不同模型或版本的Embedding；
4. 删除文档时通过外键级联清理Chunk和向量；
5. 查询只使用`active` Chunk，归档资料不会参与回答。

## 4. 知识入库

Dry-run不会调用Embedding API，也不会修改数据库：

```bash
sudo bash deploy/ecs/ingest-knowledge.sh --dry-run
```

正式生成缺失或变化的向量：

```bash
sudo bash deploy/ecs/ingest-knowledge.sh
```

强制重新生成全部向量：

```bash
sudo bash deploy/ecs/ingest-knowledge.sh --force
```

入库器每批最多提交10个文本，先完成全部Embedding调用，再在一个数据库事务中发布元数据、Chunk和新增向量。Embedding阶段失败不会把现有知识索引切换到半完成状态。

每次完整入库会先把原有Chunk标记为`archived`，随后把本次仍存在的Chunk幂等恢复为`active`。因此`AGENTIC_RAG_SOURCE_PATHS`应表示当前完整的权威知识集合，而不是一次临时增量文件。

## 5. 三段式开关

### Off

```dotenv
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=off
```

不调用Embedding和pgvector。系统行为与升级前一致。这也是新代码首次部署时的安全默认值。

### Shadow

```dotenv
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=shadow
```

系统真实执行Dense召回，但不把Dense候选放入最终融合，只在消息元数据中记录：

- `candidate_count`；
- `top_similarity`；
- `top_chunk_ids`；
- `embedding_latency_ms`；
- `vector_query_latency_ms`；
- `status`与`reason`。

Shadow用于确认模型、索引、阈值、成本和延迟，不改变用户答案。

### Hybrid

```dotenv
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=hybrid
```

Dense候选正式进入Weighted RRF和领域重排。只有完成Shadow检查和消融评测后才能打开。

## 6. 配置

```dotenv
AGENTIC_RAG_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_EMBEDDING_API_KEY=仅保存在未提交的env文件中
AGENTIC_RAG_EMBEDDING_MODEL=text-embedding-v4
AGENTIC_RAG_EMBEDDING_DIMENSIONS=1024
AGENTIC_RAG_EMBEDDING_VERSION=text-embedding-v4-1024-v1
AGENTIC_RAG_EMBEDDING_TIMEOUT_SECONDS=15
AGENTIC_RAG_DENSE_CANDIDATE_K=40
AGENTIC_RAG_DENSE_MIN_SIMILARITY=0.45
AGENTIC_RAG_DENSE_RRF_WEIGHT=1.0
```

数据库列固定为1024维，不能只改环境变量切换维度。模型或预处理策略改变时，应修改`EMBEDDING_VERSION`并重新入库。维度变化需要新增迁移或新表。

## 7. 消融评测

评测命令只调用Embedding和检索，不调用生成模型：

```bash
sudo docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T worker python -m agentic_rag.knowledge.evaluate \
  --suite smoke \
  --output /tmp/vector-eval.json
```

报告同时输出：

- `lexical_advanced`；
- `dense_only`；
- `hybrid_pgvector`。

指标为Recall@1、Recall@3、Recall@5、MRR和平均检索延迟。正式知识库接入后，还应在`eval/cases/`增加口语改写、同义词、缩写、精确系统名、旧政策冲突和越界问题。

## 8. ECS安全上线顺序

### 8.1 备份

在替换数据库镜像前执行逻辑备份，并确认文件非空：

```bash
cd "$HOME/agentic-rag"
sudo docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T postgres pg_dump -U agentic_rag -d agentic_rag \
  > "$HOME/agentic-rag-before-pgvector.sql"

ls -lh "$HOME/agentic-rag-before-pgvector.sql"
```

### 8.2 准备镜像

固定使用：

```text
pgvector/pgvector:0.8.6-pg17-bookworm
```

大陆ECS访问Docker Hub不稳定时，先把该镜像导入阿里云ACR个人仓库，再把`POSTGRES_IMAGE`改成ACR地址。不要继续使用不含扩展的`public.ecr.aws/docker/library/postgres:17-alpine`，否则Alembic执行`CREATE EXTENSION vector`会失败。

### 8.3 首次部署

保持：

```dotenv
AGENTIC_RAG_DENSE_RETRIEVAL_MODE=off
```

然后部署。PostgreSQL主版本仍然是17，Compose复用原命名卷；Alembic会创建扩展和新表。

```bash
sudo ./deploy/ecs/deploy-staging.sh
```

验证：

```bash
sudo docker compose \
  --env-file deploy/env/staging.env \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.staging.yml \
  exec -T postgres psql -U agentic_rag -d agentic_rag \
  -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
```

### 8.4 入库与Shadow

1. 设置Embedding URL和Key；
2. 执行`ingest-knowledge.sh --dry-run`；
3. 正式执行`ingest-knowledge.sh`；
4. 把模式改成`shadow`并重新部署；
5. 观察消息元数据和延迟；
6. 运行消融评测。

### 8.5 Hybrid

确认Dense来源正确、延迟可接受、无高频fallback后，将模式改为`hybrid`并重新部署。随后重新执行真实千问功能测试和2/5并发模型测试。

## 9. 回滚

算法回滚只需要把模式改回`off`并重启Worker，不需要删除向量或回滚数据库迁移。

数据库镜像已经切换并创建`vector`类型后，不要直接切回不含pgvector二进制的普通PostgreSQL镜像。需要彻底回滚数据库结构时，应使用升级前的SQL备份恢复到新的普通PostgreSQL 17数据卷。

## 10. 当前边界

- 当前没有在线`qwen3-rerank`，只预留Dense候选和诊断边界；
- 正式学校资料仍需按manifest治理后接入；
- 1024维和权重是初始工程参数，不是最终效果结论；
- 当前单Worker会串行处理生成任务，Dense本身不是主要吞吐瓶颈；
- pgvector适合当前规模，知识块增长到数十万并出现明显数据库资源竞争时，再评估独立Qdrant或托管向量服务。
