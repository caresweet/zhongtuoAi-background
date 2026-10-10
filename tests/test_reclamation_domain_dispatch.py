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
    assert cat.appendix_chapter_no("reclamation") is None
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

def _render(tmp_path, domain, chapters, filled):
    from app.services.report_assembler import ReportAssembler
    state = {"session_id": "t", "_domain": domain,
             "chapters": chapters, "filled_data": filled}
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
    assert "附件清单" not in text                     # 没有稳评第14章附件
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
    assert cat.appendix_chapter_no("reclamation") is None
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


def test_reclamation_title_page_omits_empty_rows(tmp_path):
    """抽不到的字段**整行不出现** —— 不印占位符，也不为它弹窗问用户。"""
    _, text = _render(tmp_path, "reclamation",
                      {1: {"markdown": "# 前言\n\n## 1.1 编制背景及过程\n\nX"}},
                      {"project_name": "某复垦项目", "doc_reference": "苏复垦〔2026〕1号"})
    for lab in ("单位地址：", "联 系 人：", "电　　话：", "送审时间："):
        assert lab not in text, lab


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
