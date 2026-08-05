# 项目结构与发布边界

Agentic RAG 当前采用“可运行 V1 基线 + 渐进式 V2 重做”的迁移策略。代码仓库必须能明确区分受支持代码、历史原型、知识治理元数据、本地原始资料和运行产物。

## 当前结构

```text
agentic-rag/
├─ agentic_rag_v1/             # 可运行的 V1 基线 RAG、API、CLI 与评测代码
├─ agentic_rag_v1_server.py                # 旧调用方式兼容入口
├─ tests/                   # V1 单元与回归测试
├─ eval/cases/              # 小型版本化评测集
├─ knowledge/               # schema、manifest 规则与脱敏 fixture
├─ docs/                    # 重做方案、部署、评测和模型接入文档
├─ legacy/prototype/        # 不受支持的早期实验代码
├─ .github/workflows/       # 持续集成
└─ pyproject.toml           # Python 构建和工具配置
```

下列目录只存在于开发机或后续对象存储，不进入 Git：

```text
Documents/                 # 原始 PDF 等资料
QQ/                        # 原始聊天记录
data/                      # 本地原始文档
.agentic_rag_v1_index/             # 生成索引
faiss_QA/ faiss_TEST/      # 历史索引
reports/                   # 生成的评测报告
knowledge/raw/             # V2 原始数据区
knowledge/normalized/      # V2 标准化数据区
knowledge/artifacts/       # V2 chunks/embedding/graph 产物
```

## V2 迁移原则

1. V1 保持可运行，作为效果和性能基线。
2. 新能力进入 `apps/` 与 `src/agentic_rag/` 前，必须有明确职责和可执行代码，不创建表演性空目录。
3. 旧脚本仅归档，不再从生产入口调用。
4. 原始资料通过摄取接口或对象存储提供；Git 只保存 schema、manifest 和脱敏 fixture。
5. 每个阶段同时交付测试、配置、部署说明和可重复的验证结果。

## 提交前检查

- `.env.local`、日志、索引、报告和原始知识文件未被跟踪；
- 代码中没有真实 API key、访问令牌或私钥；
- `python -X utf8 -m unittest discover -s tests -v` 通过；
- `python -m compileall -q agentic_rag_v1_server.py agentic_rag_v1 tests` 通过；
- 文档清楚标识“已经实现”和“规划中”的能力。
