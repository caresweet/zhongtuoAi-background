"""复垦域分发收口 —— 装配器与单章提示词按域取，稳评逐字不变。

背景（本分支要治的病）：域标记 `_domain` 早已贯穿生成（`domain_of_state`），
但两个下游层仍写死稳评：
  · `report_assembler` —— 无条件把**稳评评审表**塞进复垦报告的第 3 章，
    并因此**丢弃复垦第 3 章（项目概况）的正文**；附件章也按稳评第 14 章挂。
  · `master_orchestrator.build_chapter_prompt` —— 把稳评的章结构/字数/表格/口径
    注进**每一章**的提示词，包括复垦章。

修法：两个消费者一律经 `app.agent.chapter_catalog` 按域取值，自己不按域分支。
稳评是默认域，取值逐字等于改动前 —— 本文件的 `*_stability_*` 用例是那条护栏。
"""
import pytest
from docx import Document

from app.agent import chapter_catalog as cat


# ════════════════════════════════════════════════════════════
# A. chapter_catalog —— 按域取值的唯一收口
# ════════════════════════════════════════════════════════════

def test_reclamation_catalog_values():
    assert cat.all_chapters("reclamation") == list(range(1, 11))
    assert cat.total_chapters("reclamation") == 10
    assert cat.chapter_title("reclamation", 3) == "项目概况"
    assert cat.chapter_kind("reclamation", 3) == "fact"       # 叙述章，不是评审表
    assert cat.chapter_titles("reclamation")[1] == "前言"
    # 🔴 复垦没有评审表章、没有附件章 —— 这是「不塞评审表、不挂稳评附件」的判据
    assert cat.has_review_table("reclamation") is False
    assert cat.appendix_chapter_no("reclamation") == 14   # 样本目录里附件就是第14部分
    # 字数取自复垦定义里已有的 min/max
    assert cat.target_words("reclamation", 3) == (1500, 2450)


def test_stability_catalog_values_unchanged():
    """稳评（含不传域的默认）逐字等于改动前 —— 稳评护栏。"""
    assert cat.has_review_table("stability") is True
    assert cat.has_review_table(None) is True
    assert cat.appendix_chapter_no("stability") == 14
    assert cat.appendix_chapter_no(None) == 14
    assert cat.chapter_title(None, 3) == cat.chapter_title("stability", 3)
    # 稳评的字数/字段标注委托既有实现，不得出现第二份
    from app.services.master_orchestrator import target_words, stability_key_labels
    for n in range(1, 15):
        assert cat.target_words("stability", n) == target_words(n)
        assert cat.target_words(None, n) == target_words(n)
    assert cat.key_labels("stability") == stability_key_labels()
    # 报告类型名（页眉/文件名用）
    assert cat.report_kind_name("stability") == "社会稳定风险评估报告"
    assert cat.report_kind_name(None) == "社会稳定风险评估报告"
    assert cat.cover_type_line("stability") == "《社会稳定性风险评估》"
    assert cat.cover_fallback_title("stability") == "土地征收决策"


def test_catalog_sections_and_table_ref_by_domain():
    from app.agent.chapter_definitions import format_sections_for_prompt
    # 复垦第 3 章的二级标题是 3.1~3.4，不是稳评评审表的（一）（二）
    rec = cat.sections_for_prompt("reclamation", 3)
    assert "## 3.1 项目简介" in rec and "## 3.4 项目区土地利用状况" in rec
    assert "（一）" not in rec
    # 稳评第 3 章的规范由既有实现给出（逐字不变）
    assert cat.sections_for_prompt("stability", 3) == format_sections_for_prompt(3)
    assert cat.sections_for_prompt(None, 3) == format_sections_for_prompt(3)
    # 复垦第 4 章有结构规定的表；第 3 章没有 → 空串
    assert "临时用地土地利用现状统计表" in cat.table_format_reference("reclamation", 4)
    assert cat.table_format_reference("reclamation", 3) == ""


# ════════════════════════════════════════════════════════════
# B. build_chapter_prompt —— 提示词里的「结构」按域取
# ════════════════════════════════════════════════════════════

_STAB_LEAK_MARKERS = ["社会稳定风险评估", "稳评", "DB32", "征地", "征收", "评审表"]


def _chapter_def(num, title):
    return {"num": num, "title": title, "key_points": ["要点"],
            "data_needed": ["project_name"], "depends_on": [], "argument_note": ""}


def test_stability_prompt_unchanged_by_default_domain():
    """不传 domain == 显式传 stability —— 默认路径逐字不变。"""
    from app.services.master_orchestrator import build_chapter_prompt
    cd = _chapter_def(3, "社会稳定风险评估评审表")
    filled = {"project_name": "某项目", "area_mu": "1.5"}
    a = build_chapter_prompt(cd, filled)
    b = build_chapter_prompt(cd, filled, domain="stability")
    assert a == b
    # 稳评身份首句与评审表章特定要求仍在
    assert "你是社会稳定风险评估报告的资深编写专家。" in a
    assert "社会稳定风险评估评审表" in a


def test_reclamation_prompt_carries_reclamation_structure_only():
    """复垦提示词：含复垦章结构/字数，不含任何稳评结构/口径。"""
    from app.services.master_orchestrator import build_chapter_prompt
    from app.agent.reclamation_definitions import (
        RECLAMATION_ALL_CHAPTERS, RECLAMATION_CHAPTER_DEFINITIONS,
        get_reclamation_chapter_title)

    for n in RECLAMATION_ALL_CHAPTERS:
        cd = _chapter_def(n, get_reclamation_chapter_title(n))
        p = build_chapter_prompt(cd, {"project_name": "某复垦项目"}, domain="reclamation")
        # 身份是复垦的
        assert "土地复垦方案编制专家" in p
        # 复垦的结构（二级标题）在、稳评的评审表章特定要求不在
        for s in RECLAMATION_CHAPTER_DEFINITIONS[n]["sections"]:
            assert f"## {s}" in p, f"第{n}章缺小节 {s}"
        # 🔴 一字都不许带稳评的结构/口径
        for m in _STAB_LEAK_MARKERS:
            assert m not in p, f"第{n}章复垦提示词混入稳评标记「{m}」"
        # 字数是复垦区间（复垦第3章上限 2450，稳评第3章是 2000）
        lo, hi = cat.target_words("reclamation", n)
        assert f"本章必须达到{lo}字以上" in p
        assert f"本章不要超过 {hi} 字" in p


def test_reclamation_prompt_ch3_is_a_narrative_not_review_table():
    """复垦第 3 章提示词必须让它写「项目概况」正文，而不是稳评评审表。"""
    from app.services.master_orchestrator import build_chapter_prompt
    p = build_chapter_prompt(_chapter_def(3, "项目概况"), {"project_name": "X"},
                             domain="reclamation")
    assert "## 3.1 项目简介" in p
    assert "3.4 项目区土地利用状况" in p


# ════════════════════════════════════════════════════════════
# C. 装配器 —— 端到端渲染（用 tmp 目录，不碰真实 storage / 库）
# ════════════════════════════════════════════════════════════

def _render(tmp_path, domain, chapters, filled, extra_state=None):
    from app.services.report_assembler import ReportAssembler
    state = {"session_id": "t", "_domain": domain,
             "chapters": chapters, "filled_data": filled}
    if extra_state:
        state.update(extra_state)
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble(state)
    doc = Document(str(tmp_path / "generated" / out.split("/")[-1]))
    text_parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            text_parts.append(" | ".join(c.text for c in row.cells))
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            text_parts.append("HDR:" + p.text)
    return out, "\n".join(text_parts)


def test_reclamation_render_writes_ch3_and_has_no_review_table(tmp_path):
    """复垦：第3章正文写出来；没有评审表；封面/页眉/文件名按复垦。"""
    out, text = _render(tmp_path, "reclamation",
                        {3: {"markdown": "# 项目概况\n\n## 3.1 项目简介\n\n"
                                            "本项目为测试复垦项目RECLAMATIONBODY。"}},
                        {"project_name": "测试复垦项目", "doc_reference": "苏复垦〔2026〕1号"})
    assert "RECLAMATIONBODY" in text                 # 第3章正文没被丢弃
    assert "评审表" not in text                       # 没塞稳评评审表
    assert "社会稳定性风险评估" not in text           # 封面副标题不是稳评
    assert "社会稳定风险评估" not in text
    # 🔴 复垦现在**有**自己的第14部分附件（22 项），但绝不能出现稳评那套逐项名
    for _w in ("机构及人员资质材料", "拟征地公告", "专家评审", "稳评问卷调查表"):
        assert _w not in text, _w
    assert "土地复垦方案" in out                      # 文件名按复垦
    assert "HDR:测试复垦项目土地复垦方案" in text


def test_stability_render_keeps_review_table_and_discards_ch3_llm_body(tmp_path):
    """稳评：第3章仍是评审表（LLM 正文被丢弃）；封面是稳评。"""
    out, text = _render(tmp_path, "stability",
                        {3: {"markdown": "# 社会稳定风险评估评审表\n\nLLMBODY"},
                         4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nSTABILITYBODY"}},
                        {"project_name": "测试稳评项目", "doc_reference": "苏政发〔2026〕1号"})
    assert "评审表" in text                           # 评审表路径照走
    assert "LLMBODY" not in text                      # 第3章 LLM 正文照旧被丢弃
    assert "STABILITYBODY" in text                    # 其它章正文照写
    assert "《社会稳定性风险评估》" in text           # 封面副标题未变
    assert "社会稳定风险评估报告" in out              # 文件名未变
    assert "附件清单" in text                         # 第14章附件照挂


def test_reclamation_chapter_and_appendix_decisions_are_domain_gated():
    """决策点（不渲染）：装配器按域决定有无评审表路径、有无附件章。"""
    assert cat.has_review_table("reclamation") is False
    assert cat.has_review_table("stability") is True
    assert cat.appendix_chapter_no("reclamation") == 14   # 样本目录里附件就是第14部分
    assert cat.appendix_chapter_no("stability") == 14


# ════════════════════════════════════════════════════════════
# D. 编排层把域透传给提示词构造器（少了这一行，复垦仍拿稳评提示词）
# ════════════════════════════════════════════════════════════

def test_workflow_generate_passes_domain_to_prompt_builder():
    import inspect
    from app.services import report_workflow
    src = inspect.getsource(report_workflow)
    assert "build_chapter_prompt(" in src
    # 调用点必须带上 domain=_domain（域在 node_chapter_generate 里已解析）
    call = src.index("prompt = build_chapter_prompt(")
    assert "domain=_domain" in src[call:call + 400]


# ════════════════════════════════════════════════════════════
# E. 前置件文案（封面落款 / 公司简介 / 资质页标题 / 批注作者）按域取
#    这些字串原先写死在装配器里，复垦报告照样盖稳评抬头 —— 同一类泄漏。
# ════════════════════════════════════════════════════════════

def test_front_matter_labels_per_domain():
    """标签解析：稳评逐字等于改动前，复垦不含任何稳评标记。"""
    stab = cat.front_matter_labels("stability")
    # 🔴 稳评（默认域）逐字等于装配器里原来写死的那几处 —— 稳评护栏
    assert stab["org_label"] == "稳评责任主体："
    assert stab["implement_label"] == "稳评实施单位："
    assert stab["roster_heading"] == "稳评工作组人员及分工情况"
    assert stab["roster_lead_role"] == "稳评负责人"
    assert stab["cert_section_heading"] == "社会稳定风险评估业务培训证书"
    assert stab["material_note"] == "本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。"
    assert stab["map_caption"] == "拟征收土地位置示意图"
    assert stab["audit_comment_author"] == "稳评审核Agent"
    # 不传域 == 显式 stability
    assert cat.front_matter_labels(None) == stab

    rec = cat.front_matter_labels("reclamation")
    for k, v in rec.items():
        for m in ("稳评", "社会稳定风险评估", "评审表", "DB32", "征地", "征收"):
            assert m not in v, f"复垦前置件标签「{k}={v}」混入稳评标记「{m}」"
    # 🔴 封面落款抬头取自两份复垦样本的封面：
    #    「项目单位：中铁建工集团有限公司 / 编制单位：江苏众拓测绘有限公司」——
    #    复垦用「项目单位 / 编制单位」，不是稳评的「责任主体 / 实施单位」。
    assert rec["org_label"] == "项目单位："
    assert rec["implement_label"] == "编制单位："
    assert rec["roster_heading"] == "复垦方案编制人员及分工情况"
    assert rec["roster_lead_role"] == "复垦方案负责人"


def test_reclamation_render_front_matter_has_no_stability_wording(tmp_path):
    """端到端：复垦报告正文（含封面落款/公司简介）一处稳评措辞都不许有。"""
    _, text = _render(tmp_path, "reclamation",
                      {3: {"markdown": "# 项目概况\n\n## 3.1 项目简介\n\nRECLBODY"}},
                      {"project_name": "测试复垦项目", "doc_reference": "苏复垦〔2026〕1号",
                       "implement_unit": "某某复垦公司"})
    for m in ("稳评", "社会稳定风险评估", "评审表", "DB32", "征地", "征收", "拟征收"):
        assert m not in text, f"复垦报告混入稳评标记「{m}」"
    # 复垦口径的前置件文案确实盖上了
    assert "编制单位：某某复垦公司" in text
    # 🔴 复垦**没有**公司资质证书页、**没有**公司简介/人员名单页 ——
    #    两份样本（明远佳苑二期报告书、盐业井神方案）的前置里都没有；
    #    那两样在复垦里属**附件**（样本附件清单第 3 项「编制单位资质证书」）。
    assert "公司简介" not in text
    assert "公司资质证书" not in text
    assert "复垦方案编制人员及分工情况" not in text


def test_stability_render_front_matter_labels_unchanged(tmp_path):
    """稳评：前置件文案逐字不变（封面落款 + 工作组标题）。"""
    _, text = _render(tmp_path, "stability",
                      {4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nSTABBODY"}},
                      {"project_name": "测试稳评项目", "doc_reference": "苏政发〔2026〕1号",
                       "implement_unit": "某某稳评公司"})
    assert "稳评实施单位：某某稳评公司" in text
    assert "稳评工作组人员及分工情况" in text


# ════════════════════════════════════════════════════════════
# F. 前置件清单 / 章标题体例（按域接缝）
#    规格取自两份复垦样本（明远佳苑二期报告书、盐业井神方案），两样本一致：
#      封面(项目单位/编制单位/年月) → 扉页 → 目录 → 「1 前言 / 1.1 编制背景及过程」
#    样本前置里**没有**公司资质证书页、**没有**公司简介页（含人员名单）。
#    稳评（默认域）逐字等于改动前 —— 下面 `*_stability*` 是那条护栏。
# ════════════════════════════════════════════════════════════

def test_front_matter_order_per_domain():
    """前置件清单：稳评四件逐字不变；复垦无资质/简介页、有扉页。"""
    assert cat.front_matter_order("stability") == \
        ["cover", "qualifications", "intro", "toc"]
    assert cat.front_matter_order(None) == ["cover", "qualifications", "intro", "toc"]
    assert cat.front_matter_order("reclamation") == ["cover", "title_page", "toc"]


def test_has_title_page_per_domain():
    assert cat.has_title_page("stability") is False
    assert cat.has_title_page("reclamation") is True


def test_typography_per_domain():
    """字体字号按域取。稳评逐字等于改动前；复垦取四份样本一致值。

    样本实测（明远路/王元佳苑/污水处理厂/宁淮城际，四份取值完全一致）：
      一级 黑体 16pt 居中 · 二级 黑体 15pt · 三级 黑体 14pt · 正文 仿宋 12pt
    稳评是 一级 黑体 **22pt**（业主选型）· 二三级 宋体 · 正文 宋体 —— 复垦**不是**这套。
    """
    # 稳评：逐字等于改动前
    assert cat.typography("stability", "h1") == ("黑体", 22)
    assert cat.typography(None, "h1") == ("黑体", 22)
    assert cat.typography("stability", "h2") == ("宋体", 15)
    assert cat.typography("stability", "h3") == ("宋体", 14)
    assert cat.typography("stability", "body") == ("宋体", 12)
    # 复垦：按样本
    assert cat.typography("reclamation", "h1") == ("黑体", 16)
    assert cat.typography("reclamation", "h2") == ("黑体", 15)
    assert cat.typography("reclamation", "h3") == ("黑体", 14)
    assert cat.typography("reclamation", "body") == ("仿宋", 12)
    # 封面/扉页/目录：复垦字体也不同
    assert cat.typography("reclamation", "cover_sign") == ("黑体", 15)
    assert cat.typography("reclamation", "title_page_info") == ("仿宋", 14)
    assert cat.typography("reclamation", "toc_l1")[0] == "黑体"
    assert cat.typography("reclamation", "toc_l2")[0] == "仿宋"
    assert cat.typography("stability", "toc_l1")[0] == "宋体"
    # 未知槽位回落稳评，不抛异常
    assert cat.typography("reclamation", "不存在")[0] == "宋体"


def test_body_image_anchor_per_domain():
    """正文配图落位锚点：稳评**全部 None**（落位仍在装配器那两处写死分支里，逐字不动）；
    复垦第3/4章各有锚点小节。"""
    for ch in range(1, 15):
        assert cat.body_image_anchor("stability", ch) is None
        assert cat.body_image_anchor(None, ch) is None
    assert cat.body_image_anchor("reclamation", 3) == "项目简介"
    assert cat.body_image_anchor("reclamation", 4) == "土地损毁分析与评定"
    assert cat.body_image_anchor("reclamation", 1) is None
    assert cat.body_image_anchor("reclamation", 10) is None


def test_cover_org_value_per_domain():
    """封面落款第一行的值：稳评逐字取 org_name；复垦取项目单位（用地/建设单位）。

    实测 2026-10-10 明远佳苑：封面照旧取 org_name 时印的是「淮安市自然资源和规划局」
    （那是公告机关），而扉页取 project_unit 印的是「中铁建工集团有限公司」——
    同一份报告两页两个「项目单位」。复垦必须与扉页同源。
    """
    filled = {"project_unit": "中铁建工集团有限公司"}
    # 稳评：逐字等于改动前的 org_name，不受 project_unit 影响
    assert cat.cover_org_value("stability", filled, "洪泽区人民政府") == "洪泽区人民政府"
    assert cat.cover_org_value(None, filled, "洪泽区人民政府") == "洪泽区人民政府"
    # 复垦：优先 project_unit
    assert cat.cover_org_value("reclamation", filled, "淮安市自然资源和规划局") \
        == "中铁建工集团有限公司"
    # 复垦抽不到 project_unit → 退回 org_name（不印空行）
    assert cat.cover_org_value("reclamation", {}, "淮安市自然资源和规划局") \
        == "淮安市自然资源和规划局"
    assert cat.cover_org_value("reclamation", None, "淮安市自然资源和规划局") \
        == "淮安市自然资源和规划局"


def test_chapter_heading_text_per_domain():
    """稳评逐字等于改动前 `_add_chapter_title_page` 的 f"第{cn}章、{title}"。"""
    assert cat.chapter_heading_text("stability", 3, "三", "评估过程、方法和依据") \
        == "第三章、评估过程、方法和依据"
    assert cat.chapter_heading_text(None, 1, "一", "编制说明") == "第一章、编制说明"
    # 复垦取样本体例：阿拉伯数字 + 空格
    assert cat.chapter_heading_text("reclamation", 1, None, "前言") == "1 前言"
    assert cat.chapter_heading_text("reclamation", 10, None, "保障措施") == "10 保障措施"


def test_chapter_title_own_page_per_domain():
    assert cat.chapter_title_own_page("stability") is True
    assert cat.chapter_title_own_page("reclamation") is False


def test_has_chinese_sub_headings_per_domain():
    assert cat.has_chinese_sub_headings("stability") is True
    assert cat.has_chinese_sub_headings(None) is True
    assert cat.has_chinese_sub_headings("reclamation") is False


def test_is_chapter_heading_text_per_domain():
    """目录缓存据此跳过前置页标题 —— 复垦章标题是阿拉伯数字，稳评正则会漏。"""
    assert cat.is_chapter_heading_text("stability", "第三章、评估过程") is True
    assert cat.is_chapter_heading_text("stability", "公司简介") is False
    assert cat.is_chapter_heading_text("reclamation", "3 项目概况") is True
    assert cat.is_chapter_heading_text("reclamation", "公司简介") is False


def test_normalize_reclamation_headings():
    """LLM 退回「（一）/ 加粗」体例时，按章节目录把小节拉回 `X.Y`。

    这是「（一）项目简介」的真凶修复：单章后处理原用**稳评**标准标题集比对，
    复垦的 `## 3.1 项目简介` 不在集合里 → 被改写成 `**（一）项目简介**`。
    """
    md = ("## （一）项目简介\n\n正文\n\n"
          "### 项目区自然概况\n\n正文\n\n"
          "**（三）社会经济概况**\n\n正文\n\n"
          "## 没在小节表里的标题\n\n保留\n")
    out = cat.normalize_reclamation_headings(3, md)
    assert "## 3.1 项目简介" in out
    assert "## 3.2 项目区自然概况" in out
    assert "## 3.3 社会经济概况" in out
    assert "（一）" not in out and "（三）" not in out
    # 认不出的标题原样保留，不硬改
    assert "## 没在小节表里的标题" in out


def test_normalize_reclamation_headings_bare_line_items():
    """裸行的「（一）表土剥离」也要编号 —— 装配器本来就把它当标题渲染。

    实测第6章 LLM 写成裸行，原先的归一化只认 `###`／`**加粗**`，5 条「（一）…（五）」
    原样印进报告：没编号、也没层级，与全篇 `X.Y.Z` 体例不一致。
    """
    md = ("## 6.1 工程设计\n"
          "### 6.1.1 工程技术措施设计\n"
          "（一）表土剥离\n"
          "临时用地施工前……\n"
          "（二）场地清理\n"
          "### 6.1.2 生物化学措施设计\n"
          "（一）监测措施\n"
          "## 6.2 工程量测算\n")
    out = cat.normalize_reclamation_headings(6, md)
    # 归到**它所属的那一级**（6.1.1 之下 → 6.1.1.1），不是 6.1 那层
    assert "#### 6.1.1.1 表土剥离" in out
    assert "#### 6.1.1.2 场地清理" in out
    # 换到 6.1.2 后序号从 1 重排，不会续成 6.1.1.3
    assert "#### 6.1.2.1 监测措施" in out
    assert "（一）" not in out and "（二）" not in out
    # 正文段落不受影响
    assert "临时用地施工前……" in out


def test_normalize_reclamation_headings_bare_line_guard():
    """裸行判据与装配器逐字一致（短、无句读）—— 正文里以「（一）」开头的**句子**不许改。"""
    md = ("## 6.1 工程设计\n"
          "（一）表土剥离应结合表土厚度与土源条件确定，施工前完成，剥离后集中堆存并做好防护。\n")
    out = cat.normalize_reclamation_headings(6, md)
    assert out.splitlines()[1].startswith("（一）表土剥离应结合")


def test_normalize_reclamation_headings_noop_for_stability_chapter_number():
    """章号不在复垦目录里（如 11）→ 原样返回，不误改。"""
    md = "## 3.1 项目简介\n\n正文\n"
    assert cat.normalize_reclamation_headings(11, md) == md



def test_reclamation_render_has_title_page(tmp_path):
    """复垦扉页（样本规格）：项目名称/项目单位/单位地址/联系人/电话/送审时间。"""
    _, text = _render(tmp_path, "reclamation",
                      {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nX"}},
                      {"project_name": "某复垦项目", "doc_reference": "苏复垦〔2026〕1号",
                       "project_unit": "某某建设集团有限公司",
                       "unit_address": "某市某区某路1号",
                       "contact_name": "张三", "contact_phone": "13800000000",
                       "submit_date": "2026-10-01"})
    for s in ("项目单位：某某建设集团有限公司", "单位地址：某市某区某路1号",
              "联 系 人：张三", "电　　话：13800000000", "送审时间：2026-10-01"):
        assert s in text, s


def test_reclamation_title_page_keeps_all_six_rows(tmp_path):
    """扉页按样本**六行齐全**，抽不到的字段留**空横线**给手填。

    🔴 这条断言 2026-10-10 被业主推翻过：原设计是「值为空则整行不输出」，
       于是材料里没有「单位地址/送审时间」时扉页只剩 4 行，业主一眼看出
       「信息还缺少了几项」。样本（明远路 第2页）是 6 行填表、每行一条横线 ——
       横线在就等于留了空位，**不是占位符**（仍不印「待填写」这类字）。
    """
    _, text = _render(tmp_path, "reclamation",
                      {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nX"}},
                      {"project_name": "某复垦项目", "doc_reference": "苏复垦〔2026〕1号"})
    for lab in ("项目名称：", "项目单位：", "单位地址：", "联 系 人：", "电　　话：", "送审时间："):
        assert lab in text, lab
    assert "【待补充】" not in text          # 空值留横线，但不印占位符字


def test_stability_render_keeps_qualifications_and_intro(tmp_path):
    """稳评护栏：资质页/简介页/人员名单照旧（前置件清单四件全在），且**没有**扉页。"""
    _, text = _render(tmp_path, "stability",
                      {4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nB"}},
                      {"project_name": "某稳评项目", "doc_reference": "苏政发〔2026〕1号"})
    assert "公司简介" in text
    assert "稳评工作组人员及分工情况" in text
    assert "送审时间：" not in text          # 稳评没有扉页


def test_normalize_reclamation_headings_sub_items():
    """小节下的「（一）（二）」→ 当前小节的三级编号（样本体例 `1.2.1`）。"""
    md = ("## 1.1 编制背景及过程\n\n正文\n\n"
          "## （一）复垦方案服务年限\n\nA\n\n## （二）各类土地面积\n\nB\n")
    out = cat.normalize_reclamation_headings(1, md)
    assert "### 1.1.1 复垦方案服务年限" in out
    assert "### 1.1.2 各类土地面积" in out
    assert "（一）" not in out and "（二）" not in out


def test_normalize_reclamation_headings_renumbers_restarted_group():
    """LLM 把同一小节分成两段、序号各自从「（一）」重来 —— 按出现顺序重排，不出重号。"""
    md = ("## 6.1 工程设计\n\n### （一）表土剥离\n\nA\n\n### （二）场地清理\n\nB\n\n"
          "### （一）监测措施\n\nC\n\n### （二）管护措施\n\nD\n\n## 6.2 工程量测算\n\nE\n")
    out = cat.normalize_reclamation_headings(6, md)
    for want in ("### 6.1.1 表土剥离", "### 6.1.2 场地清理",
                 "### 6.1.3 监测措施", "### 6.1.4 管护措施"):
        assert want in out, want
    assert out.count("### 6.1.1") == 1


# ════════════════════════════════════════════════════════════
# G. 版式（字体/字号）渲染护栏
#    复垦取四份样本一致值；稳评逐字等于改动前。这里落到**真实产出的 docx**上验，
#    不只看接缝函数 —— 因为 `_post_generation_fix` 会在最后再刷一遍标题字体，
#    不跟着域走的话，接缝改对了也会被它刷回稳评那套。
# ════════════════════════════════════════════════════════════

def _ea_font_and_size(p):
    """段落首个 run 的 (中文字体, 字号pt)。"""
    from docx.oxml.ns import qn as _q
    if not p.runs:
        return None, None
    r = p.runs[0]
    rPr = r._element.rPr
    ea = None
    if rPr is not None and rPr.rFonts is not None:
        ea = rPr.rFonts.get(_q('w:eastAsia'))
    return ea, (r.font.size.pt if r.font.size else None)


def _render_doc(tmp_path, domain, chapters, filled):
    from docx import Document as _D
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": domain,
                      "chapters": chapters, "filled_data": filled})
    return _D(str(tmp_path / "generated" / out.split("/")[-1]))


_CH = {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nBODYBODYTEXT\n"}}


def _pick(doc, needle):
    """按文本找段落 → (中文字体, 字号pt)。"""
    for p in doc.paragraphs:
        if p.text.strip() == needle:
            return _ea_font_and_size(p)
    return None


def test_render_typography_reclamation(tmp_path):
    """复垦产物：一级 黑体16 / 二级 黑体15 / 正文 仿宋12（四份样本一致）。"""
    doc = _render_doc(tmp_path, "reclamation", _CH, {"project_name": "测试复垦项目"})
    assert _pick(doc, "1 前言") == ("黑体", 16.0)
    assert _pick(doc, "1.1 编制背景及过程") == ("黑体", 15.0)
    assert _pick(doc, "BODYBODYTEXT") == ("仿宋", 12.0)


_CH_STAB = {1: {"markdown": "# 第一章 前言\n\n## 1.1 编制背景及过程\n\nBODYBODYTEXT\n"}}


def test_render_typography_stability_unchanged(tmp_path):
    """稳评产物：一级 黑体22（业主选型）/ 二级 宋体15 / 正文 宋体12 —— 与改动前一致。"""
    doc = _render_doc(tmp_path, "stability", _CH_STAB, {"project_name": "测试稳评项目"})
    assert _pick(doc, "第一章、编制说明") == ("黑体", 22.0)
    assert _pick(doc, "1.1 编制背景及过程") == ("宋体", 15.0)
    assert _pick(doc, "BODYBODYTEXT") == ("宋体", 12.0)


# ════════════════════════════════════════════════════════════
# H. 第14部分「附件」（复垦 22 项）
#    样本目录：10 保障措施 / 11 复垦方案报告表 / 12 请示 / 13 图件 / 14 附件。
#    本轮只实现 14；11/12/13 未做，故目录会出现 10 → 14 的跳号（刻意）。
# ════════════════════════════════════════════════════════════

def test_appendix_kind_per_domain():
    """附件块由哪套渲染器产出：两域内容毫无重叠，装配器据此二选一。"""
    assert cat.appendix_kind("reclamation") == "reclamation"
    assert cat.appendix_kind("stability") == "stability"
    assert cat.appendix_kind(None) == "stability"


def test_uses_company_archive_images_per_domain():
    """公司资质图：稳评与复垦都取公司档案；投标走另一条（asset_images 表）。"""
    assert cat.uses_company_archive_images("reclamation") is True
    assert cat.uses_company_archive_images("stability") is True
    assert cat.uses_company_archive_images("bidding") is False


def test_reclamation_post_body_chapter_titles():
    """11–14 是正文之后的部分，标题取自另一张表（不污染 CHAPTER_DEFINITIONS）。"""
    assert cat.chapter_title("reclamation", 14) == "附件"
    assert cat.chapter_title("reclamation", 13) == "图件"
    assert cat.chapter_title("reclamation", 11) == "复垦方案报告表"
    assert cat.chapter_title("reclamation", 10) == "保障措施"      # 正文章不受影响
    # 🔴 `RECLAMATION_CHAPTER_DEFINITIONS` 不能被塞进 11–14 ——
    #    否则 `chapter_titles` / `target_words` 会凭空多出四章
    from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
    assert set(RECLAMATION_CHAPTER_DEFINITIONS) == set(range(1, 11))
    assert 14 not in cat.chapter_titles("reclamation")


def test_appendix_items_match_sample_list():
    """22 项逐项抄自样本目录，不是编的。"""
    from app.agent.reclamation_definitions import RECLAMATION_APPENDIX_ITEMS
    assert [n for n, _ in RECLAMATION_APPENDIX_ITEMS] == list(range(1, 23))
    _m = dict(RECLAMATION_APPENDIX_ITEMS)
    assert _m[3] == "编制单位资质证书"
    assert _m[12] == "现场照片、影像图"
    assert _m[16] == "勘测报告"
    assert _m[20] == "主体工程批复文件"
    assert _m[22] == "其他"


def test_appendix_matcher_prefers_specific_name():
    """匹配表**专名优先** —— 顺序错了主体项目那份会被附件16 抢走。

    实测文件名是「明远佳苑二期勘测定界报告主体项目---96413.pdf」，
    「勘测定界报告」之后紧跟「主体项目」（词序与「主体项目勘测定界」相反），
    所以 18 那一条必须用「主体项目」去匹配，且排在 16 前面。
    """
    from app.agent.reclamation_definitions import match_reclamation_appendix as M
    assert M("明远佳苑二期勘测定界报告主体项目------------------96413.pdf") == 18
    assert M("勘测定界报告_81e1aaf0.pdf") == 16
    assert M("2.明远佳苑二期挂牌红线-------------------96413.pdf") == 17
    assert M("3_环保局批准文件_扫描件_----.pdf") == 7
    assert M("1.明远路以南地块商住项目核准的批复_明远佳苑.pdf") == 20
    assert M("明远佳苑二期用地批准书_用地规划许可_----.pdf") == 20
    assert M("临时工棚CH20220437 -功能分布图.DWG") == 13
    assert M("随手拍的照片.jpg") == 0                     # 认不出 → 0（归附件22）


def test_reclamation_render_has_appendices(tmp_path):
    """复垦产物含第14部分附件：22 项清单 + 逐项标题；且不含稳评那套逐项名。"""
    out, text = _render(tmp_path, "reclamation",
                        {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nBODY\n"}},
                        {"project_name": "测试复垦项目"})
    assert "附件清单" in text
    assert "附件3 编制单位资质证书" in text
    assert "附件16 勘测报告" in text
    assert "附件22 其他" in text
    # 缺材料的项要显性标出来（业主口径：材料不要有漏的）
    assert "附件1 土地复垦方案委托函（待补充）" in text
    for _w in ("机构及人员资质材料", "拟征地公告", "专家评审", "稳评问卷调查表"):
        assert _w not in text, _w


# ════════════════════════════════════════════════════════════
# I. 业主 2026-10-10 报的五处（逐条对应）
# ════════════════════════════════════════════════════════════

def test_cover_type_line_and_date_per_domain():
    """封面：复垦类型行**不带书名号**、落款第三行**无标签纯数字**。

    样本封面实测（明远路、宁淮城际）：副标题写作「土地复垦方案报告书」
    「临时用地土地复垦方案报告书」——**一律不带书名号**；落款第三行是
    「2023  年  1  月」。带书名号的《社会稳定性风险评估》是稳评被业主点名的写法，
    照抄到复垦封面上就是稳评格式（业主所指「封面第一页还是稳评的格式」）。
    """
    from datetime import datetime
    d = datetime(2026, 10, 1)
    assert cat.cover_type_line("reclamation") == "土地复垦方案报告书"
    assert cat.cover_type_line("stability") == "《社会稳定性风险评估》"   # 稳评不动
    assert cat.cover_date_parts("reclamation", d) == ("", "2026  年  10  月")
    assert cat.cover_date_parts("stability", d) == ("报告编制日期：", "二〇二六年十月")


def test_no_questionnaire_tables_for_reclamation(tmp_path):
    """复垦没有问卷 —— 不许凭空长出表9-1/9-2。

    这两张表原先是**系统无条件注入第9章**的（稳评的公众参与靠两份问卷）。
    复垦第9章是「效益分析」，注入的结果是两张人数全 0、比例全「[待分析]」的表。
    """
    assert cat.has_questionnaire_tables("stability") is True
    assert cat.has_questionnaire_tables(None) is True
    assert cat.has_questionnaire_tables("reclamation") is False
    _out, text = _render(tmp_path, "reclamation",
                         {9: {"markdown": "# 土地复垦效益分析\n\n## 9.1 社会效益评价\n\nBODY\n"}},
                         {"project_name": "测试复垦项目"})
    assert "问卷统计汇总表" not in text


def test_md_table_total_row_continuation_merged(tmp_path):
    """「合计」被 LLM 拆成两行 markdown → **并回一行**，而不是删掉或错行。

    实测表4-2 原样：
        | 合计   | — | — | — | — | — |
        | 8070.0 | 100.0 | — | — | — | — |
    旧的兜底把「只有第一格有字、其余全占位符」的行**整行删掉** ——
    于是合计行要么消失（表4-1/4-3），要么数值行留下来、8070.0 落到「序号」列（表4-2）。
    """
    md = ("# 项目概况\n\n## 3.4 项目区土地利用状况\n\n"
          "| 序号 | 地块名称 | 面积 | 占比 |\n"
          "| --- | --- | --- | --- |\n"
          "| 1 | 临时用地 | 8070.0 | 100.0 |\n"
          "| 合计 | — | — | — |\n"
          "| 8070.0 | 100.0 | — | — |\n")
    _out, text = _render(tmp_path, "reclamation", {3: {"markdown": md}},
                         {"project_name": "测试复垦项目"})
    _flat = " ".join(text.split())
    assert "合计 | 8070.0 | 100.0" in _flat, _flat
    # 续行不再单独成行（原先 8070.0 会落到「序号」列）
    for _ln in text.splitlines():
        assert not _ln.strip().startswith("8070.0 |"), _ln


def test_trailing_images_survive_post_fix(tmp_path):
    """文档**末尾的图**不许被「清理末尾空白段」删掉。

    那段清理只看 `<w:t>` 文字判断是否空段，而插图段一个 `w:t` 都没有 ——
    实测附件20 的 7 页扫描件被整段删光（文档停在标题那行，
    `word/media` 里留 7 个没人引用的孤儿图）。
    """
    import zipfile
    PILImage = pytest.importorskip("PIL.Image")
    img = tmp_path / "tail.png"
    PILImage.new("RGB", (40, 30), (240, 240, 240)).save(img)
    out, _text = _render(tmp_path, "reclamation",
                         {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nBODY\n"}},
                         {"project_name": "测试复垦项目"})
    _p = tmp_path / "generated" / out.split("/")[-1]
    # 在文档末尾追加一张图再跑一遍后处理，图必须还在
    from docx import Document as _D
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler(storage_dir=tmp_path)
    a._domain = "reclamation"
    doc = _D(str(_p))
    doc.add_paragraph().add_run().add_picture(str(img))
    a._post_generation_fix(doc, {})
    xml = doc.element.body.xml
    assert xml.count("<w:drawing>") >= 1


def test_land_use_table_rendered_from_land_classification(tmp_path):
    """表4-2 由**勘测定界的地类数据**按样本结构渲染，不再用 LLM 那行「以耕地为主」。

    样本（明远路 表4-4）是按地类逐项 + 合计；数据源是勘测定界的「土地分类面积表」，
    由 `land_class_extract` 视觉读出后落在 `filled_data["land_classification"]`。
    """
    md = ("# 土地复垦方向可行性分析\n\n## 4.2 复垦区土地利用状况\n\n"
          "表4-2 临时用地土地利用现状统计表\n\n"
          "| 序号 | 地块名称 | 面积() | 占比(%) |\n"
          "| --- | --- | --- | --- |\n"
          "| 1 | 临时用地 | 8070.0 | 100.0 |\n")
    filled = {"project_name": "测试复垦项目",
              "land_classification": {
                  "rows": [{"一级地类": "耕地", "二级地类": "水田", "面积_m2": 478.0},
                           {"一级地类": "林地", "二级地类": "其他林地", "面积_m2": 2211.0},
                           {"一级地类": "交通运输用地", "二级地类": "公路用地", "面积_m2": 2727.0}],
                  "合计_m2": 5416.0}}
    _out, text = _render(tmp_path, "reclamation", {4: {"markdown": md}}, filled)
    assert "一级地类" in text and "二级地类" in text
    assert "水田" in text and "其他林地" in text and "公路用地" in text
    assert "0.0478" in text and "0.2211" in text and "0.2727" in text
    assert "0.5416" in text                       # 合计
    assert "以耕地为主" not in text                # LLM 那行被拦掉了
    # 面积/占比都按数据算，不是抄的
    assert "8.83" in text                         # 0.0478 / 0.5416 × 100（四舍五入）


def test_ch6_requirements_block_is_chapter_specific():
    """第6章专属要求只并进第6章：必须调 `calc_earthwork`、给实数、单位照样本。

    实测业主指出「表6-1 格式和数据也不对」：分项全写「以现场核定为准」，
    单位写成 `100m`（样本是 `100m³`）。面积其实已经有了（事实卡「地类构成」，
    由勘测定界的土地分类面积表视觉读出），工具也早注册好了 —— 缺的只是明确要求。
    """
    b6 = cat.requirements_block("reclamation", 1200, 1600, 6)
    assert "本章专属要求（第6章" in b6
    assert "calc_earthwork" in b6
    assert "100m³" in b6
    # 单位那关不只靠提示词：见 `_normalize_qty_units`（模型实测把 100m³ 写成 100m）
    for _ch in (1, 4, 7, 10):
        assert "本章专属要求" not in cat.requirements_block("reclamation", 1000, 2000, _ch)
    # 不传章号 = 改动前逐字相同；稳评永远返回空串（它走内联正文）
    assert cat.requirements_block("reclamation", 1200, 1600) == \
        cat.requirements_block("reclamation", 1200, 1600, None)
    assert cat.requirements_block("stability", 100, 200, 6) == ""


def test_qty_units_normalized_per_sample(tmp_path):
    """复垦工程量表的单位**确定性归一** —— 模型实测把 `100m³` 写成 `100m`。

    提示词里明写了单位，模型还是丢立方（样本 6.2 的量表单位是固定的四种）。
    """
    md = ("# 土地复垦工程设计及工程量测算\n\n## 6.2 工程量测算\n\n"
          "表6-1 土地复垦工程量测算表\n\n"
          "| 序号 | 工程或费用名称 | 单位 | 数量 |\n"
          "| --- | --- | --- | --- |\n"
          "| 一 | 土壤重构工程 | — | — |\n"
          "| 1 | 表土剥离 | 100m | 8.1 |\n"
          "| 2 | 土地翻耕 | m | 0.05 |\n"
          "| 3 | 田面平整 | 100m | 80.7 |\n"
          "| 4 | 土壤培肥（有机肥） | t | 0.7 |\n")
    _out, text = _render(tmp_path, "reclamation", {6: {"markdown": md}},
                         {"project_name": "测试复垦项目"})
    assert "100m³" in text          # 表土剥离
    assert "公顷" in text            # 土地翻耕
    assert "100m²" in text           # 田面平整
    assert "吨" in text              # 土壤培肥
    assert "| 100m |" not in text and "| m |" not in text


def test_chapter_no_of_text_per_domain():
    """段文本 → 章号：复垦「1 前言」、稳评「第三章、…」；都不是则 0。

    🔴 审核批注的锚点原先是写死稳评正则的 —— 复垦章标题一条都匹配不上，
       所有批注回落到 `doc.paragraphs[0]`（封面标题），交付稿首页挂满批注气球。
    """
    assert cat.chapter_no_of_text("reclamation", "1 前言") == 1
    assert cat.chapter_no_of_text("reclamation", "14 附件") == 14
    assert cat.chapter_no_of_text("reclamation", "第三章、评估过程") == 0
    assert cat.chapter_no_of_text("reclamation", "1.1 编制背景及过程") == 0   # 小节不是章
    assert cat.chapter_no_of_text("reclamation", "明远路以南地块商住项目") == 0
    assert cat.chapter_no_of_text("stability", "第三章、评估过程") == 3
    assert cat.chapter_no_of_text("stability", "第11章 分析预测风险源") == 11
    assert cat.chapter_no_of_text("stability", "1 前言") == 0


def test_reclamation_has_no_word_audit_comments(tmp_path):
    """复垦产物里**不许有 Word 批注** —— 缺口改在生成结束后由前端提示。

    业主 2026-10-10 口径：「不需要你写这个，这些内容你可以在项目生成报告结束后
    跟用户提示或者用个方式说明」。以前缺口写成批注挂在章节上（交付稿里带一批批注
    气球）；而且锚点用的还是稳评的 `^第X章`，复垦一条都匹配不上，
    9 条批注全落到封面标题（首页挂满气球）。

    稳评不变（仍注入，逐字等于改动前）。
    """
    import zipfile
    assert cat.injects_audit_comments("reclamation") is False
    assert cat.injects_audit_comments("stability") is True
    assert cat.injects_audit_comments(None) is True
    _gaps = {1: [{"detail": "第1章缺 X", "severity": "warn", "sentence": "第一句"}],
             3: [{"detail": "第3章缺 Y", "severity": "info", "sentence": "第三句"}]}
    out, text = _render(tmp_path, "reclamation",
                        {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nBODY\n"},
                         3: {"markdown": "# 项目概况\n\n## 3.1 项目简介\n\nBODY3\n"}},
                        {"project_name": "测试复垦项目"},
                        extra_state={"_chapter_gaps": _gaps})
    _p = tmp_path / "generated" / out.split("/")[-1]
    assert "word/comments.xml" not in zipfile.ZipFile(str(_p)).namelist()
    assert "待补充/待核对" not in text


# ════════════════════════════════════════════════════════════
# J. 业主 2026-10-12 报的：封面/扉页字重与行距、表1-1/表3-1 归属
# ════════════════════════════════════════════════════════════

def test_cover_and_title_page_weights_and_gaps():
    """封面/扉页**不加粗**，且行间留空档（业主：「中间都要隔一行」「没有平均占满一页」）。

    样本实测（明远路 p1/p2）：
      封面 三行标题 26.1pt 黑体不加粗、落款 15pt 黑体不加粗，行距 56pt（字 15pt）；
      扉页 标题 22pt 不加粗、信息行 14.1pt 仿宋不加粗，六行均匀铺满整页。
    稳评仍加粗、落款紧排（逐字等于改动前）。
    """
    assert cat.cover_bold("reclamation") is False
    assert cat.cover_bold("stability") is True
    assert cat.cover_bold(None) is True
    assert cat.front_matter_gap_pt("reclamation", "cover_sign") > 20      # 隔一行
    assert cat.front_matter_gap_pt("reclamation", "title_page_row") > 40  # 铺满
    assert cat.front_matter_gap_pt("stability", "cover_sign") == 0.0      # 稳评不变
    assert cat.front_matter_gap_pt("stability", "title_page_row") == 22.0


def _table_by_header(doc, needle):
    from docx.table import Table as _T
    from docx.oxml.ns import qn as _q
    for el in doc.element.body.iterchildren():
        if el.tag == _q('w:tbl'):
            tb = _T(el, doc)
            if needle in " ".join(c.text for c in tb.rows[0].cells):
                return tb
    return None


def _render_doc_full(tmp_path, chapters, filled):
    import asyncio
    from docx import Document as _D
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": "reclamation",
                      "chapters": chapters, "filled_data": filled})
    return _D(str(tmp_path / "generated" / out.split("/")[-1]))


def test_ch1_renders_sample_damage_table(tmp_path):
    """第1章的表按样本换成「临时用地拟损毁土地情况表」（6 列 + 合计）。"""
    md = ("# 前言\n\n## 1.2 复垦方案摘要\n\n"
          "表1-1 临时用地土地利用现状地类构成表\n\n"
          "| 一级地类 | 二级地类 | 面积() |\n| --- | --- | --- |\n"
          "| 耕地 | 水田 | 478.0 |\n| 合计 | 8070.0 | — |\n")
    doc = _render_doc_full(tmp_path, {1: {"markdown": md}},
                           {"project_name": "测试复垦项目",
                            "land_classification": {"rows": [{"一级地类": "耕地", "二级地类": "水田",
                                                              "面积_m2": 478.0}], "合计_m2": 8070.0}})
    tb = _table_by_header(doc, "临时用地名称")
    assert tb is not None, "第1章应渲染「临时用地拟损毁土地情况表」"
    _rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
    assert _rows[0][1] == "面积（公顷）"
    assert _rows[1] == ["施工用地", "0.8070", "1", "压占", "重度", "待复垦"]
    assert _rows[2][0] == "合计" and _rows[2][1] == "0.8070"


def test_ch3_land_use_table_is_dropped(tmp_path):
    """第3章**不设**土地利用现状表（样本第3章无表；地类数据归第4章表4-4）。

    实测 LLM 会在第1、第3章各编一张同名地类表，与第4章那张重复，
    且合计行写串列（`| 合计 | 8070.0 | — |` 把面积塞进「二级地类」列）。
    """
    md = ("# 项目概况\n\n## 3.4 项目区土地利用状况\n\n"
          "表3-1 临时用地土地利用现状地类表\n\n"
          "| 一级地类 | 二级地类 | 面积() | 占比(%) |\n| --- | --- | --- | --- |\n"
          "| 耕地 | 水田 | 478.0 | 5.9 |\n| 合计 | 8070.0 | 100.0 | — |\n")
    doc = _render_doc_full(tmp_path, {3: {"markdown": md}},
                           {"project_name": "测试复垦项目",
                            "land_classification": {"rows": [{"一级地类": "耕地", "二级地类": "水田",
                                                              "面积_m2": 478.0}], "合计_m2": 8070.0}})
    assert _table_by_header(doc, "一级地类") is None, "第3章不该出现土地利用现状表"


# ════════════════════════════════════════════════════════════
# K. 第11部分「复垦方案报告表」（样本 p57–p60 那种制式表单）
# ════════════════════════════════════════════════════════════

def test_report_form_chapter_per_domain():
    """复垦有第11部分「复垦方案报告表」；稳评没有（它第11章是「分析预测风险源」）。"""
    assert cat.report_form_chapter_no("reclamation") == 11
    assert cat.report_form_chapter_no("stability") is None
    assert cat.report_form_chapter_no(None) is None
    assert cat.chapter_title("reclamation", 11) == "复垦方案报告表"


def test_report_form_render(tmp_path):
    """报告表渲染：竖排分节名 + 样本那几张表 + 主要编制人员子表。

    样本（明远路 p57）是一整张带边框的大表，**最左列放竖排的分节名**
    （「项/目/概/况」逐字竖排），右侧是「标签｜值｜标签｜值」的表单。
    """
    from docx import Document as _D
    from docx.oxml.ns import qn as _q

    # 用一个带完整事实的 filled，避免依赖外部公司/人员库
    filled = {"project_name": "某复垦项目", "project_unit": "某某公司",
              "location": "淮安市某区", "area_m2": 8070.0, "contact_phone": "13800000000",
              "construction_nature": "新建", "project_investment": 9500,
              "project_investment_unit": "万元",
              "land_classification": {"rows": [
                  {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 478.0}],
                  "合计_m2": 8070.0}}
    doc = _render_doc_full(tmp_path, {8: {"markdown": "# 工作计划\n\n正文甲\n"},
                                      10: {"markdown": "# 保障措施\n\n正文乙\n"}},
                           filled)
    _titles = []
    _tables = []
    for el in doc.element.body.iterchildren():
        if el.tag == _q('w:p'):
            _titles.append(el)
        elif el.tag == _q('w:tbl'):
            _tables.append(__import__('docx').table.Table(el, doc))
    _all_text = "\n".join(t.text for t in _titles)
    assert "土地复垦方案报告表" in _all_text
    # 四个竖排分节名（逐字换行）
    _joined = "\n".join("".join(c.text for c in tb.rows[0].cells) for tb in _tables)
    for _sec in ("项目概况", "方案编制单位", "复垦区土地利用现状", "复垦土地面积", "投资估算"):
        assert _sec in _joined.replace("\n", ""), _sec
    # 样本那几张表的关键列
    _hdr = "\n".join(" | ".join(c.text for c in tb.rows[0].cells) for tb in _tables)
    assert "临时用地" not in _hdr            # 报告表里没有拟损毁表（那是第1章）
    assert "工程或费用名称" in _hdr or "主要编制人员" in _joined.replace(" ", "")


def test_report_form_absent_for_stability(tmp_path):
    """稳评护栏：不渲染报告表。"""
    _out, text = _render(tmp_path, "stability",
                         {4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nB"}},
                         {"project_name": "测试稳评项目"})
    assert "土地复垦方案报告表" not in text


# ════════════════════════════════════════════════════════════
# D. 第4章补渲的分析表（表4-8/4-11/4-12/4-13）—— 确定性，逐格回验样本
# ════════════════════════════════════════════════════════════

_CH4_MD = ("# 土地复垦方向可行性分析\n\n"
           "## 4.4 土地复垦适宜性评价\n\n本节论述适宜性。\n\n"
           "## 4.5 水土资源平衡分析\n\n本节论述水土资源。\n\n"
           "## 4.7 复垦的目标任务\n\n本节论述目标任务。\n")

_CH4_FILLED = {
    "project_name": "测试复垦项目", "area_m2": 8070.0,
    "land_classification": {"合计_m2": 8070.0, "rows": [
        {"一级地类": "耕地（01）", "二级地类": "水田（0101）", "面积_m2": 478.0},
        {"一级地类": "水域及水利设施用地（11）", "二级地类": "沟渠（1107）", "面积_m2": 74.0},
        {"一级地类": "林地（03）", "二级地类": "其他林地（0307）", "面积_m2": 2211.0},
        {"一级地类": "住宅用地（07）", "二级地类": "农村宅基地（0702）", "面积_m2": 2580.0},
        {"一级地类": "交通运输用地（10）", "二级地类": "公路用地（1003）", "面积_m2": 2727.0}]},
}


def test_ch4_analysis_tables_render(tmp_path):
    """第4章补渲四张分析表（样本 13 张表里可确定性推导的那四张）。"""
    doc = _render_doc_full(tmp_path, {4: {"markdown": _CH4_MD}}, _CH4_FILLED)
    caps = "\n".join(p.text for p in doc.paragraphs)
    for _c in ("表4-8 土地复垦主导限制因素", "表4-11 临时用地表土资源平衡分析表",
               "表4-12 临时用地水资源分析表", "表4-13 临时用地复垦前复垦后结构对比表"):
        assert _c in caps, _c


def test_ch4_soil_balance_matches_sample_numbers(tmp_path):
    """表4-11 逐格回验样本：0.2689 公顷 × 0.2 m = 537.8 m³（剥覆平衡）。"""
    doc = _render_doc_full(tmp_path, {4: {"markdown": _CH4_MD}}, _CH4_FILLED)
    tb = _table_by_header(doc, "可供土方量")
    assert tb is not None
    rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
    assert rows[1][1] == "0.2689" and rows[1][2] == "0.2" and rows[1][3] == "537.8"
    assert rows[2][6] == "537.8"
    assert rows[3][3] == "537.8" and rows[3][6] == "537.8"


def test_ch4_before_after_structure_uses_pct(tmp_path):
    """表4-13 复垦前/后结构对比：占比按面积算（样本 5.92 / 27.4 / 33.79 …）。"""
    doc = _render_doc_full(tmp_path, {4: {"markdown": _CH4_MD}}, _CH4_FILLED)
    tb = _table_by_header(doc, "复垦前面积")
    rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
    assert rows[-1][1] == "0.8070" and rows[-1][2] == "100"
    _pcts = {r[0]: r[2] for r in rows[1:-1]}
    assert _pcts["耕地（01） 水田（0101）"] == "5.92"
    assert _pcts["林地（03） 其他林地（0307）"] == "27.4"


def test_ch4_tables_land_under_their_sections(tmp_path):
    """表4-8 落在 4.5 之前、表4-13 落在 4.5 之后 —— 按小节号归位，不是全堆章末。"""
    doc = _render_doc_full(tmp_path, {4: {"markdown": _CH4_MD}}, _CH4_FILLED)
    order = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    assert any(t.startswith("## 4.5") or t.endswith("水土资源平衡分析") for t in order)
    i45 = next(i for i, t in enumerate(order) if "水土资源平衡分析" in t)
    i48 = next(i for i, t in enumerate(order) if t.startswith("表4-8"))
    i413 = next(i for i, t in enumerate(order) if t.startswith("表4-13"))
    assert i48 < i45 < i413


def test_ch4_tables_skipped_when_model_wrote_its_own(tmp_path):
    """模型已自己写了同题表 → 系统不重复补（避免两张同题表）。"""
    md = _CH4_MD + "\n表4-13 临时用地复垦前复垦后结构对比表\n\n| 地类 | 面积 |\n| --- | --- |\n| 耕地 | 0.0478 |\n"
    doc = _render_doc_full(tmp_path, {4: {"markdown": md}}, _CH4_FILLED)
    caps = [p.text for p in doc.paragraphs if p.text.startswith("表4-13")]
    assert len(caps) <= 1, caps


def test_ch4_tables_absent_for_stability(tmp_path):
    """稳评护栏：第4章不补复垦的分析表。"""
    _out, text = _render(tmp_path, "stability",
                         {4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nB"}},
                         {"project_name": "测试稳评项目"})
    assert "表4-11 临时用地表土资源平衡分析表" not in text
    assert "表4-13 临时用地复垦前复垦后结构对比表" not in text


# ════════════════════════════════════════════════════════════
# E. 报告表列宽（样本 p57 表格线逐列量取）
# ════════════════════════════════════════════════════════════

def test_report_form_column_widths_follow_sample(tmp_path):
    """报告表列宽 = 样本 p57 的表格线（pt 83.1/139.7/187.5/279.1/388.5/453.5/526.3）。

    主表单 5 格：段名 2.00 ｜ 标签 4.92 ｜ 值 3.86 ｜ 标签 2.29 ｜ 值 2.57（cm）。
    """
    doc = _render_doc_full(
        tmp_path, {10: {"markdown": "# 保障措施\n\n正文甲\n"}},
        {"project_name": "测试复垦项目"})
    tb = None
    for t in doc.tables:
        if "项目概况" in "".join(c.text for c in t.rows[0].cells).replace("\n", ""):
            tb = t
            break
    assert tb is not None, "报告表「项目概况」分节未渲染"
    _row = tb.rows[3]                     # 法人代表｜联系电话 —— 四个格全分列
    got = [round(c.width.cm, 2) for c in _row.cells]
    assert got == [2.00, 4.92, 3.86, 2.29, 2.57], got


# ════════════════════════════════════════════════════════════
# F. 字数不足 → 定向续写（复垦专开）
# ════════════════════════════════════════════════════════════

def test_splice_supplements_into_named_sections():
    from app.agent.agents.chapters.chapter_base import ChapterAgentBase
    md = "## 4.1 甲\n\n正文甲\n\n## 4.5 乙\n\n正文乙\n\n## 4.7 丙\n\n正文丙\n"
    add = "[[4.5]]\n\n补充乙段。\n\n[[4.1]]\n\n补充甲段。\n"
    out = ChapterAgentBase._splice_supplements(md, add)
    assert out.index("补充甲段") < out.index("## 4.5")
    assert out.index("## 4.5") < out.index("补充乙段") < out.index("## 4.7")


def test_splice_supplements_unknown_section_goes_to_tail():
    from app.agent.agents.chapters.chapter_base import ChapterAgentBase
    md = "## 4.1 甲\n\n正文甲\n"
    out = ChapterAgentBase._splice_supplements(md, "[[9.9]]\n\n补充段。\n")
    assert out.rstrip().endswith("补充段。")


def test_top_up_gate_is_reclamation_only():
    from app.agent import chapter_catalog as cc
    assert cc.tops_up_short_chapters("reclamation") is True
    assert cc.tops_up_short_chapters("stability") is False
    assert cc.has_analysis_tables_in_body("reclamation") is True
    assert cc.has_analysis_tables_in_body("stability") is False


def test_plain_char_count_ignores_markdown_syntax():
    """字数按**读者看到的字**算：`##`/`**`/表格竖线与分隔行都不计。

    实测第3章带记法 1636 字（过 1500 的下限）、落进 docx 只剩 1312 字（不到）。
    """
    from app.agent.agents.chapters.chapter_base import ChapterAgentBase as B
    md = "## 3.1 项目简介\n\n**本项目**位于项目区。\n\n| 地类 | 面积 |\n| --- | --- |\n| 耕地 | 0.0478 |\n"
    assert B._plain_char_count(md) == len("3.1项目简介本项目位于项目区。地类面积耕地0.0478")
    assert B._plain_char_count("## 4.1 甲\n") < len("## 4.1 甲")


def test_splice_supplements_without_markers_appends_instead_of_dropping():
    """模型没按 `[[小节号]]` 回复时，**整块挂章末**，不丢内容。

    早先 `if not blocks: return md` —— 一次 LLM 调用白花，字数一点没涨
    （实测复垦第4章续写后仍是 4489 字，远低于下限 7500）。
    """
    from app.agent.agents.chapters.chapter_base import ChapterAgentBase
    md = "## 4.5 水土资源平衡分析\n\n正文。\n"
    add = "好的，以下是补充内容：\n\n这一段是补充的论述，没有小节号标记。\n"
    out = ChapterAgentBase._splice_supplements(md, add)
    assert out != md
    assert "这一段是补充的论述" in out
    assert "好的，以下是" not in out
