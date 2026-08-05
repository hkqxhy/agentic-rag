# Legacy prototype

This directory preserves the original Agentic RAG experiments for reference during the V2 migration.

- `prototype/` contains the early LangChain, local-Qwen, embedding, and demo scripts.
- These files are not part of the supported runtime and may depend on machine-specific paths or obsolete packages.
- The supported V1 baseline remains `agentic_rag_v1/` with `agentic_rag_v1_server.py` as its compatibility entry point.
- Delete legacy code only after the V2 implementation passes retrieval, answer-quality, and performance gates.

Do not copy credentials or generated indexes into this directory.
