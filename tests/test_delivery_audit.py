# -*- coding: utf-8 -*-
"""交付体检（80% 可用率目标）的单元测试。

业主口径（2026-10-30）：「每个报告生成之后要达到 80% 可用率，
并且用户只需要看一遍就行。」这里钉住口径本身：怎么算分母、怎么判「要人动」。
"""
from app.services.delivery_audit import audit_chapters, summarize, paragraphs_of


def _chap(*paras):
    return {"markdown": "\n\n".join(paras)}


def test_clean_report_hits_full_rate():
    ch = {1: _chap(
        "洪拟征告〔2026〕7号土地征收决策位于淮安市洪泽区，涉及2个村、43户、186人。",
        "征收土地面积合计6.5517公顷，其中农用地6.2公顷、建设用地0.3517公顷。",
    )}
    a = audit_chapters(ch)
    assert a["rate"] == 1.0
    assert a["below_target"] is False


def test_placeholder_paragraph_is_flagged():
    ch = {3: _chap("项目征地面积【待补充】，详见勘测定界报告。")}
    a = audit_chapters(ch)
    assert a["flagged"] == 1
    assert a["by_kind"]["placeholder"] == 1
    assert a["below_target"] is True


def test_buzzword_paragraph_is_flagged():
    ch = {5: _chap("通过多措并举、统筹推进，为项目实施提供了有力保障。")}
    a = audit_chapters(ch)
    assert a["by_kind"].get("ai_tone") == 1


def test_vague_paragraph_without_numbers_is_flagged():
    ch = {8: _chap("各有关部门要高度重视，有关单位要狠抓落实，扎实推进各项工作。")}
    a = audit_chapters(ch)
    assert a["by_kind"].get("vague") == 1


def test_headings_tables_and_images_are_not_counted():
    md = "\n".join([
        "## 3.1 项目简介",
        "| 项目 | 数值 |",
        "| --- | --- |",
        "![图3-1](x.png)",
        "张兴矿区位于淮安市淮安区石塘镇境内，距离淮安市区约23公里。",
    ])
    paras = list(paragraphs_of(md))
    assert len(paras) == 1


def test_rate_math_and_summary():
    ch = {
        1: _chap("洪拟征告〔2026〕7号土地征收决策涉及43户、186人，面积6.5517公顷。",
                 "工作程序按《土地管理法》第四十七条执行，已完成公告与听证。"),
        2: _chap("各有关部门要高度重视，有关单位要狠抓落实。"),
    }
    a = audit_chapters(ch)
    assert a["total"] == 3
    assert a["flagged"] == 1
    assert round(a["rate"], 4) == round(2 / 3, 4)
    assert "可用率" in summarize(a)


# ── 断长句：体检里占比最大的一类，靠装配前确定性处理 ──

def test_split_long_sentence_breaks_at_comma_only():
    from app.validation.content_guardrails import split_long_sentences

    s = ("洪拟征告〔2026〕7号土地征收决策位于淮安市洪泽区高良涧街道境内，"
         "征收土地面积合计6.5517公顷，其中农用地6.2公顷、建设用地0.3517公顷，"
         "涉及2个村43户186人，评估工作自2026年3月启动至2026年5月完成。")
    out, n = split_long_sentences(s)
    assert n >= 1
    # 顿号是并列项，绝不能被当成句号断开
    assert "6.2公顷、建设用地" in out
    # 断开后的句子不应再有 60 字以上的
    for piece in out.split("。"):
        assert len(piece) <= 60


def test_split_skips_quoted_and_short_text():
    from app.validation.content_guardrails import split_long_sentences

    # 书名号里的逗号不能断
    s = "依据《江苏省土地管理条例》第三条的规定，本决策按规定程序开展了社会稳定风险评估工作并形成评估报告报送有关部门备案。"
    out, _ = split_long_sentences(s)
    assert "《江苏省土地管理条例》第三条" in out

    short = "本项目位于淮安市洪泽区。"
    out2, n2 = split_long_sentences(short)
    assert out2 == short and n2 == 0
