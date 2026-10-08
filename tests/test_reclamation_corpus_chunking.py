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


def test_bare_text_pdf_style_sections_are_classified(tmp_path):
    """钉住两处「不这么做就跑不通」的修正。

    ⚠️ **简报原有的三条测试只覆盖带 `#` 的 markdown 与第一章**，所以：
      - 「没有带标题的 section 就回落 `chunk_text`」这条回落未被断言；
      - 「标题优先」的判定顺序未被断言（原来的 `chapter.title` 合成键会把
        **整个第 4 章**都归成 `方向-适宜性`）。
    把任一处改回简报的写法，原有测试**仍会全绿**，而分类已经错了。

    本用例模拟 **PDF/docx 抽取出的裸文本**（无 `#`；小节号与标题同行、正文紧跟其后），
    一次覆盖两处。

    ⚠️ 输入形态以引擎真实行为为准：`chunk_text` 把「标题占一整段」的段落当 section
    标题，段间**必须夹一个独立正文段**，否则紧邻的下一个标题会把这节丢掉（首节
    无正文段 → 被丢弃）。故此处两节之间放一段正文，确保两节都成块。
    """
    p = tmp_path / "bare.txt"
    p.write_text(
        "4.1.1 土地损毁环节与时序\n"
        "本项目施工便道采用压占方式损毁土地。\n\n"
        "复垦工作按阶段实施，损毁环节为施工期。\n\n"
        "4.4.1 评价原则\n"
        "复垦适宜性评价遵循因地制宜的原则。\n",
        encoding="utf-8",
    )
    chunks = build_chunks(str(p))
    colls = {c["collection"] for c in chunks}
    assert "kb_reclamation_damage" in colls        # 4.1.1 → 方向-损毁分析
    assert "kb_reclamation_suitability" in colls   # 4.4.1 → 方向-适宜性

