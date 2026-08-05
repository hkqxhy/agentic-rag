# 贡献指南

Agentic RAG 采用“V1 基线可复现、V2 分阶段迁移”的方式开发。每个改动都应能说明它改善了哪项业务质量、工程可靠性或交付能力。

## 开发流程

1. 从 `main` 创建短生命周期分支；
2. 只提交当前任务相关文件；
3. 新配置必须提供无密钥的示例；
4. 新功能必须补充单元、集成、契约、评测或负载测试中的至少一种；
5. 行为变化必须更新 README、架构文档或 ADR；
6. 合并前运行本地验证并确认没有原始知识文件或运行产物被跟踪。

## 本地验证

```bash
python -m pip install -e ".[dev]"
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q agentic_rag_v1_server.py agentic_rag_v1 tests
```

Ruff 和 mypy 配置已经写入 `pyproject.toml`。在 V2 迁移过程中逐模块收紧门禁，不为通过检查而大范围改写不相关的 V1 基线。

## 数据贡献

不要直接提交 PDF、聊天导出、生成索引或包含个人信息的测试样例。数据贡献应先完成：

- 来源与授权确认；
- 个人信息脱敏；
- 权威等级、适用校区/年级和有效期标注；
- checksum 与版本记录；
- 人工复核和回归评测。

允许进入 Git 的知识内容仅限 `knowledge/manifests/`、`knowledge/schemas/` 和脱敏的 `knowledge/fixtures/`。

## 提交信息

推荐使用清晰的 Conventional Commits 风格，例如：

```text
feat(retrieval): add metadata-aware hybrid query
fix(api): release generation permit after SSE cancellation
test(eval): add stale-policy refusal cases
docs(architecture): record model gateway decision
```
