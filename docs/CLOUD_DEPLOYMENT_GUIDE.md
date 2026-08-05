# Agentic RAG 公有云部署与千问接入方案

> 更新日期：2026-08-04
> 已确认前提：阿里云 ECS 个人免费试用 4 核 8 GB；控制台预计约 600 小时；约 100 名注册用户、峰值 10—20 人在线；模型使用阿里云百炼千问 API；不购买 GPU；首版使用普通应用账号；学校资料暂由管理员维护，保留自动同步接口。

## 1. 最终选择

初版开发、集成、压测、评测和演示统一使用 **阿里云 ECS 4C8G 免费试用实例**。若控制台允许选择地域，优先选择与百炼工作空间相同的 **华北 2（北京）**，减少跨地域模型调用的不确定性；若现有试用实例已在其他中国大陆地域，不必为了这一点重建，先实测端到端延迟。

这台机器对当前目标完全够用：

- 模型生成、Embedding 和 Rerank 都在百炼云端完成，ECS 不需要 GPU；
- 4C8G 足以同时运行 Web、FastAPI/LangGraph、PostgreSQL、Redis、Qdrant、异步 Worker 和轻量观测组件；
- 当前知识索引约 26,000 个 chunks，单机 Qdrant 的容量压力较小；
- 目标只有约 100 名注册用户、5—10 条同时生成流，瓶颈更可能是模型 API 延迟和限流，而不是本地 CPU；
- 相比 2C2G，不需要为了省内存删掉数据库、队列或可观测性，能展示完整工程能力。

但它是一个 **有时间窗口的工程验收环境**，不是长期生产承诺：

1. 阿里云当前个人试用按 3 个月有效期内的 300 元试用额度抵扣，个人规格最高 4C8G、每小时最高抵扣 0.833 元；控制台显示的约 600 小时取决于所选实例实际小时价格，不应把它理解成所有配置都固定赠送 600 小时。
2. 600 小时连续运行约等于 25 天；使用“节省停机模式”按需开机后，可以覆盖数月的分阶段开发和验收。
3. 中国大陆地域每月免费公网下行流量额度有限，当前官方规则为 20 GB/月。文本问答和 SSE 足够使用，但不应让用户通过 ECS 下载大体积原始文档。
4. 当前免费试用是按量付费实例，不满足中国大陆 ICP 备案条件。因此它适合部署测试、压测和面试演示，不适合宣称为长期面向公众运营的正式站点。
5. 单机架构不是高可用架构。容量通过不代表可用区、实例或磁盘故障时仍可服务。

## 2. 容量模型与资源预算

V1 按以下规模验证：

- 约 100 名注册用户；
- 峰值 10—20 人在线；
- 常规峰值 1 个新问题/秒，短时突发 2 个/秒；
- 最多 5—10 条同时生成流；
- 每月折算 2,000—10,000 个问题；
- 目标不是证明“服务过 100 名真实用户”，而是证明“按 100 用户规模设计并完成压测验证”。

4C8G 建议资源上限：

```text
4 vCPU / 8 GB RAM
├─ Ubuntu + Docker                         0.8—1.0 GB
├─ FastAPI / LangGraph API（2 workers）    0.8—1.2 GB
├─ Next.js Web + Caddy                    0.3—0.7 GB
├─ PostgreSQL                             0.8—1.2 GB
├─ Redis                                  0.2—0.4 GB
├─ Qdrant                                 1.0—1.8 GB
├─ Worker / OTel / 日志                   0.5—0.9 GB
└─ 文件缓存与峰值余量                      1.3—2.4 GB
```

所有容器都设置 CPU、内存和日志大小限制。不要在在线压测时同时执行 OCR、全量重建索引和完整离线评测；这些任务应进入有界队列并串行或低并发运行。

磁盘建议至少 80 GB，并按用途隔离：系统与镜像 25 GB、PostgreSQL 10—15 GB、Qdrant 15—20 GB、临时解析区 10 GB、日志 5 GB，其余作为安全余量。磁盘达到 70% 和 85% 时分级告警。

## 3. 单机部署结构

首版使用 Docker Compose，不为了简历标签提前引入 Kubernetes：

```text
Browser
  -> Caddy/Nginx :443
      -> Next.js Web
      -> FastAPI API
          -> LangGraph Agent
          -> PostgreSQL
          -> Redis / bounded queue
          -> Qdrant
          -> Ingestion Worker
          -> Alibaba Model Studio
      -> OTel Collector
```

工程要求：

- 只对公网开放 80/443；22 端口限制管理 IP；PostgreSQL、Redis、Qdrant 不开放公网；
- 容器使用私有网络，镜像使用 commit SHA 或语义版本，不使用 `latest`；
- 区分 liveness、readiness 和 dependency health；
- 数据库迁移失败时终止发布，不带病启动；
- SSE 支持取消传播、心跳、断线重连和事件游标；
- 应用层设置 10 条生成流硬上限、有界排队、超时、429/5xx 有限重试和熔断；
- 每晚备份 PostgreSQL、Qdrant snapshot 和知识 manifest 到 OSS；
- 用脚本或 CI 保留一键部署、回滚和恢复流程。

Prometheus/Grafana 可以在压测和演示期间按需启动。日常在线只保留 OTel Collector、轮转日志和必要指标，避免观测栈反过来影响被测系统。

## 4. 600 小时使用策略

建议预算如下：

| 阶段 | 预算小时 | 主要产物 |
|---|---:|---|
| 系统初始化与安全加固 | 20 | 安全组、SSH、Docker、备份基线 |
| 持续部署与集成调试 | 60 | Compose、CI/CD、HTTPS、回滚 |
| 知识导入和索引验证 | 30 | 版本化索引、发布/回滚记录 |
| RAG 与 Agent 效果评测 | 60 | 基线对比、失败案例、评测报告 |
| 峰值/突发/故障压测 | 40 | k6 报告、资源曲线、瓶颈结论 |
| 72 小时长稳测试 | 72 | 错误率、内存趋势、恢复记录 |
| 最终演示、录屏和复核 | 40 | 演示视频、截图、可复现实验 |
| **预计使用** | **322** |  |
| **排错与复测余量** | **278** |  |

节约规则：

1. 日常编码、单元测试、测试集标注、报告写作在本地完成；
2. 只有云端集成、真实网络调用、压测、长稳和演示时开启 ECS；
3. 空闲时使用阿里云“节省停机模式”，而不是只在系统内执行关机；
4. 停机前先推送代码和镜像，备份数据库、Qdrant、知识 manifest 与评测报告；
5. 节省停机模式会释放计算资源，固定公网 IP 也可能被释放，重启时还可能因库存不足暂时无法分配同规格资源；依赖 IP 白名单和 DNS 的配置必须重新检查；
6. 系统盘、数据盘、快照等资源停止时仍可能计费或消耗试用额度，必须在费用中心观察实际明细；
7. 设置 50%、70%、85%、95% 额度告警，95% 时停止非必要实验。

## 5. ECS 初始化与安全

1. 个人阿里云账号完成实名认证并领取 ECS 试用；
2. 选择 4C8G、Ubuntu 24.04 LTS、中国大陆地域；
3. 创建非 root 管理用户，使用 SSH 密钥，关闭密码登录和 root 远程登录；
4. 安全组只开放受限的 22、80、443；
5. 安装 Docker Engine 与 Compose plugin；
6. 设置时区、NTP、日志轮转、自动安全更新与磁盘告警；
7. 创建 OSS bucket 保存备份和评测产物，使用 RAM 子账号的最小权限凭证；
8. 在费用中心开启试用额度、百炼调用、公网流量和存储告警；
9. 不在免费试用实例上办理 ICP 备案，也不把临时 IP 当作长期入口；
10. 面试演示可直接使用临时 HTTPS 域名或本地端口转发；若需要长期公开域名，试用结束后迁移到满足备案条件的包年包月实例。

## 6. 百炼千问普通账号接入

### 6.1 云账号和工作空间

1. 使用个人阿里云主账号完成实名认证；
2. 开通百炼 Model Studio；
3. 在 **华北 2（北京）** 创建独立的 Agentic RAG 业务空间；
4. 开通按量付费，设置每日/月度预算和异常消费告警；
5. 创建 RAM 管理用户处理日常控制台配置，应用运行时不使用主账号 AccessKey；
6. 创建只允许访问所需模型的 API Key；
7. API Key 只通过服务端 Secret 或环境变量注入，不进入 Git、前端、镜像和日志。

普通应用账号与阿里云账号是两套体系。最终用户只注册 Agentic RAG 的用户名/邮箱和密码，永远不能获得云账号凭证或百炼 API Key。

### 6.2 Model Gateway 配置

```dotenv
AGENTIC_RAG_LLM_BASE_URL=https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
AGENTIC_RAG_LLM_API_KEY=REPLACE_AT_DEPLOY_TIME
AGENTIC_RAG_LLM_SIMPLE_MODEL=qwen-plus
AGENTIC_RAG_LLM_COMPLEX_MODEL=qwen3.7-plus
AGENTIC_RAG_EMBEDDING_MODEL=text-embedding-v4
AGENTIC_RAG_EMBEDDING_DIMENSIONS=1024
AGENTIC_RAG_RERANK_MODEL=qwen3-rerank
```

仓库只提交 `.env.example`。Model Gateway 分别定义 `ChatClient`、`EmbeddingClient` 和 `RerankClient`：Chat 和普通 dense Embedding 可使用 OpenAI-compatible 客户端；dense+sparse 联合 Embedding 和 Rerank 通过百炼对应接口适配。业务代码不能直接绑定厂商 SDK。

不再安装 vLLM。vLLM 用于在自有 GPU 上部署模型；本方案把推理、批处理和扩缩容交给百炼，ECS 只承担应用、检索和数据服务。未来只有在调用量足以覆盖 GPU 固定成本、需要内网隐私或云端配额无法满足时，才重新评估自托管模型。

## 7. 普通应用账号

首版采用：

- 用户名或邮箱 + 密码注册；
- Argon2id 保存密码哈希；
- 15 分钟 Access Token + 30 天可轮换 Refresh Token；
- Refresh Token 使用 `HttpOnly + Secure + SameSite=Lax` Cookie；
- 用户/IP 双层限流和连续失败退避；
- `user`、`knowledge_editor`、`admin` 三种角色；
- 会话只能由所有者访问，后台操作写入审计日志；
- 支持注销账号和删除个人数据。

## 8. 知识库更新接口

首版不自动抓取学校资料，只实现可审计的管理员更新闭环：

```text
POST   /api/v1/admin/knowledge/uploads
POST   /api/v1/admin/knowledge/documents
GET    /api/v1/admin/knowledge/documents
PATCH  /api/v1/admin/knowledge/documents/{id}/metadata
POST   /api/v1/admin/knowledge/documents/{id}/parse
GET    /api/v1/admin/knowledge/jobs/{job_id}
POST   /api/v1/admin/knowledge/documents/{id}/validate
POST   /api/v1/admin/knowledge/documents/{id}/publish
POST   /api/v1/admin/knowledge/documents/{id}/archive
GET    /api/v1/admin/knowledge/documents/{id}/versions
POST   /api/v1/admin/knowledge/indexes/rebuild
POST   /api/v1/admin/knowledge/indexes/{version}/activate
POST   /api/v1/admin/knowledge/indexes/{version}/rollback
```

发布流程是“上传 → 解析 → 元数据补全 → 自动检查 → 预览 → 人工批准 → 影子索引 → 小型回归 → 原子切换”。数据模型预留 `source_connector`、`source_url`、`authority_level`、`effective_from/to`、`maintainer`、`content_hash` 和 `license_status`，未来增加官网同步器时无需重做知识领域模型。

## 9. 压测与验收

k6 或 Locust 必须运行在本地电脑、CI Runner 或另一台机器上，不能在被测 ECS 内运行。否则负载生成器会抢占 CPU/内存，吞吐和延迟都不可信。

至少保存四组测试：

- peak：1 QPS、5 条流，持续 30 分钟；
- spike：2 QPS、10 条流，持续 5—10 分钟；
- soak：代表性混合流量持续 72 小时；
- failure：模拟百炼 429/5xx、超时、Redis 重启、Worker 崩溃和客户端断连。

验收门槛先定义、后测量，不预造数据：

- 非模型 API p95 < 300 ms；
- 检索 p95 < 500 ms；
- 首 Token 时间 p95 < 3 s；
- 流式请求成功率 ≥ 99%；
- 峰值测试不发生 OOM，队列有界且过载时返回明确错误；
- 断线重连不产生重复消息；
- 备份可恢复，知识索引可回滚；
- 质量指标和性能指标都由机器生成报告，并记录版本、模型、提示词和索引 ID。

如果未达标，简历中写实测结果和优化过程，不把目标值写成已实现值。

## 10. 成本与试用结束后的路径

免费试用只覆盖符合规则的 ECS 计算额度。百炼 API、超出免费额度的公网流量、云盘、快照、OSS 和域名可能单独计费。100 用户低频试点可先设置较低的百炼月度硬预算，通过 FAQ 缓存、上下文压缩、简单/复杂模型路由和离线批处理控制成本。

试用结束后有三种选择：

1. 秋招展示已经完成：关闭实例，保留代码、镜像、备份、视频和报告，固定成本降为零；
2. 偶尔演示：按需购买短期 2C4G/4C8G 实例，从备份一键恢复；
3. 长期公开运营：购买满足备案条件的中国大陆包年包月实例，办理 ICP 备案，并逐步迁移数据库、缓存和向量库到托管高可用服务。

不要仅为了展示“Kubernetes”而维护空集群。先用单机 Compose 证明部署、恢复、限流、观测和容量结论；当单机故障不可接受或真实负载超过阈值时，再升级为负载均衡 + 双应用节点 + 托管 PostgreSQL/Redis + 独立向量服务。

## 11. 官方依据

- [阿里云 ECS 免费试用规则](https://help.aliyun.com/zh/ecs/user-guide/ecs-free-trial)
- [阿里云 ECS 免费试用产品页](https://free.aliyun.com/product/product/ecs/freetrial)
- [阿里云 ECS 节省停机模式](https://help.aliyun.com/zh/ecs/user-guide/economical-mode)
- [阿里云 ECS 免费试用常见问题](https://help.aliyun.com/zh/document_detail/612761.html)
- [阿里云百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- [阿里云百炼限流](https://help.aliyun.com/zh/model-studio/rate-limit)
- [阿里云百炼 API Key](https://help.aliyun.com/zh/model-studio/get-api-key/)
- [阿里云百炼首次调用千问](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen)
- [阿里云百炼流式输出](https://help.aliyun.com/zh/model-studio/stream)
- [阿里云 text-embedding-v4](https://help.aliyun.com/zh/model-studio/embedding)
- [阿里云 qwen3-rerank](https://help.aliyun.com/zh/model-studio/text-rerank-api)

活动规则、库存、模型单价和限流都会变化。每次创建实例和正式压测前重新核对控制台；项目文档记录核对日期，不把一次性试用额度当作长期单位成本。
