"""复垦范文切段：按小节归到语义类；未知小节不硬塞。"""
from scripts.ingest_reclamation_corpus import build_chunks, section_key_for


def test_section_key_mapping():
    assert section_key_for("1.1 编制背景及过程") == "前言"
    assert section_key_for("2.3 编制依据") == "编制总则"
    assert section_key_for("3.2 项目区自然概况") == "项目概况"
    assert section_key_for("4.4 土地复垦适宜性评价") == "方向-适宜性"
    assert section_key_for("7.2 估算成果") == "投资估算"
    assert section_key_for("10.6 土地权属调整方案") == "保障措施"


def test_section_key_returns_none_for_unknown():
    assert section_key_for("附录A 某表") is None


def test_build_chunks_uses_reclamation_collections(tmp_path):
    """每一块的 collection 名都必须以 kb_reclamation_ 开头。"""
    p = tmp_path / "sample.md"
    p.write_text(
        "# 1 前言\n\n## 1.1 编制背景及过程\n\n本项目为临时用地土地复垦方案，"
        "服务于张兴矿区采输卤扩建工程。\n\n## 1.2 复垦方案摘要\n\n"
        "复垦服务年限为五年。\n",
        encoding="utf-8",
    )
    chunks = build_chunks(str(p))
    assert chunks
    assert all(c["collection"].startswith("kb_reclamation_") for c in chunks)
    assert all(c["metadata"]["domain"] == "reclamation" for c in chunks)
