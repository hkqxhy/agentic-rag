from __future__ import annotations

from pathlib import Path

from agentic_rag_v1.loaders import iter_source_files, load_sources


def test_official_corpus_ignores_readme_and_parses_front_matter(tmp_path: Path) -> None:
    official = tmp_path / "knowledge" / "official"
    official.mkdir(parents=True)
    (official / "README.md").write_text("# 维护说明\n", encoding="utf-8")
    document = official / "campus-card.md"
    document.write_text(
        """---
document_id: nju-campus-card
title: 校园卡挂失与补办
authority_level: official
status: active
source_url: https://itsc.nju.edu.cn/21469/listm.htm
keywords: 校园卡, 挂失, 补办
---

# 校园卡挂失与补办

校园卡遗失后应立即挂失，再按官方流程补办。
""",
        encoding="utf-8",
    )

    assert iter_source_files([official]) == [document]

    chunks = load_sources([official])

    assert chunks
    assert all(chunk.metadata["kind"] == "document" for chunk in chunks)
    assert all(chunk.metadata["document_id"] == "nju-campus-card" for chunk in chunks)
    assert all(chunk.metadata["authority_level"] == "official" for chunk in chunks)
    assert all(chunk.metadata["status"] == "active" for chunk in chunks)
    assert all(
        chunk.metadata["source_url"] == "https://itsc.nju.edu.cn/21469/listm.htm"
        for chunk in chunks
    )
    assert chunks[0].metadata["keywords"] == ["校园卡", "挂失", "补办"]
    assert "document_id:" not in chunks[0].content
    assert not any(chunk.metadata["kind"] == "directory" for chunk in chunks)
