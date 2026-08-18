# 贡献指南

Agentic RAG 的改动应能说明它改善了哪项业务质量、工程可靠性或交付能力。当前生产主链路位于 `src/agentic_rag`，其中的检索层会复用 `src/agentic_rag_v1` 的 Advanced RAG 和 GraphRAG 实现。

## 开发流程

1. 从 main 创建短生命周期分支。
2. 每个提交只包含一项可解释的变更。
3. 新配置必须提供无密钥示例，真实凭据只放在本地或部署 Secret 中。
4. 新功能至少补充单元、集成、契约、效果评测或负载测试中的一种。
5. 行为、接口或部署方式变化时，同步更新 README 或对应文档。
6. 提交前确认没有原始知识文件、索引、日志和评测产物被跟踪。

## 开发环境

~~~bash
python -m pip install -e ".[dev]"
pnpm install --frozen-lockfile
~~~

运行 API、Worker 和 Web 的方式见根目录 [README](README.md#本地开发)。

## 提交前验证

~~~bash
ruff check src/agentic_rag tests/unit tests/integration
mypy src/agentic_rag tests/unit tests/integration tests/e2e
pytest -q tests/unit tests/test_agentic_rag_v1.py
python -m compileall -q src tests
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
docker compose -f deploy/compose/docker-compose.yml config --quiet
~~~

涉及 PostgreSQL、pgvector、Redis、Worker 或 SSE 的改动，还应运行集成测试：

~~~bash
AGENTIC_RAG_RUN_INTEGRATION=1 pytest -q tests/integration
~~~

## 知识数据

不要提交 PDF、聊天导出、生成索引或包含个人信息的测试样例。知识数据进入系统前应完成：

- 来源与授权确认；
- 个人信息脱敏；
- 权威等级、适用范围和有效期标注；
- checksum 与版本记录；
- 人工复核和回归评测。

允许进入 Git 的知识内容仅限 `knowledge/manifests`、`knowledge/schemas` 和脱敏的 `knowledge/fixtures`。

## 提交信息

建议使用 Conventional Commits：

~~~text
feat(retrieval): add metadata-aware hybrid query
fix(api): release generation permit after SSE cancellation
test(eval): add stale-policy refusal cases
docs(architecture): record model gateway decision
~~~
