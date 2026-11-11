# -*- coding: utf-8 -*-
"""专家批注后的内容修补（2026-11-11）。

每条用例的输入都是成稿里被批注的原句，断言修补后的句子。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 稳评 ─────────────────────────────────────────────────────
_AREA_MD = """根据勘测定界成果，拟征收土地总面积326342.0平方米。土地用途为商业服务业设施用地。地类构成包括耕地99434.0平方米、林地125104.0平方米、交通运输用地14989.0平方米、水域及水利设施用地59359.0平方米、园地8000.0平方米、草地11127.0平方米、其它土地3884.0平方米、特殊用地497.0平方米。土地权属涉及朱坝街道。

| 被征（用）地单位 | 权属类别 | 面积总计 | 耕地 | 园地 | 林地 | 草地 | 商业服务业用地 | 工矿用地 | 住宅用地 | 特殊用地 | 交通运输用地 | 水域及水利设施用地 | 其它土地 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 合计 | — | 326342 | 99434 | 8000 | 125104 | 11127 | 1671 | 5 | 2272 | 497 | 14989 | 59359 | 3884 | — |
"""


def test_area_sum_fills_missing_categories():
    """正文漏了商业服务业/工矿/住宅三类（3948㎡），要按表6-1补齐，而不是把总数改小。"""
    from app.services.report_content_repairs import fix_area_sum
    out, n = fix_area_sum(_AREA_MD)
    assert n == 1
    assert "总面积326342平方米" in out
    for kw in ("商业服务业用地1671平方米", "工矿用地5平方米", "住宅用地2272平方米"):
        assert kw in out


def test_risk_count_follows_table():
    from app.services.report_content_repairs import fix_risk_count
    ch = {5: {"markdown": "本决策共4个方面主要风险因素，其中5个为一般风险，1个为较小风险，识别出4项。\n\n"
                           "| 序号 | 风险类型 | 风险因素描述 | 风险等级 |\n|---|---|---|---|\n"
                           "| 1 | A | a | 一般风险 |\n| 2 | B | b | 一般风险 |\n"
                           "| 3 | C | c | 一般风险 |\n| 4 | D | d | 一般风险 |\n"
                           "| 5 | E | e | 较小风险 |\n| 6 | F | f | 较小风险 |\n"}}
    n = fix_risk_count(ch)
    assert n >= 2
    assert "共6个方面主要风险因素" in ch[5]["markdown"]
    assert "识别出6项" in ch[5]["markdown"]
    assert "4个为一般风险，2个为较小风险" in ch[5]["markdown"]


def test_date_order_moves_review_after_report():
    from app.services.report_content_repairs import fix_date_order
    md = ("2026年9月28日至9月30日编制评估报告。……"
          "2026年9月22日至9月30日组织专家评审。")
    out, n = fix_date_order(md)
    assert n == 1
    assert "2026年10月1日至10月9日组织专家评审" in out


def test_fake_law_replaced():
    from app.services.report_content_repairs import fix_fake_laws
    md = "4.《朱坝街道办事处、三圩社区、二组、三组、六组民委员会组织法》（2026年1月1日起施行）；"
    out, n = fix_fake_laws(md)
    assert n == 1
    assert "《中华人民共和国村民委员会组织法》（2018年12月29日修正）" in out


def test_garble_and_score_formula():
    from app.services.report_content_repairs import fix_garble, fix_score_formula
    g = "责任单　2026年9月24日，位审阅工作方案并提出修改意见。"
    g, n = fix_garble(g)
    assert n == 1 and "责任单位于2026年9月24日审阅工作方案" in g
    f = "群众意见分析得分 =（0.0%1 + 0.0%0.7 + 0.0%0.3）35 = 0.0分。"
    f, n = fix_score_formula(f)
    assert n == 1
    assert "0.0%×1 + 0.0%×0.7 + 0.0%×0.3）×35" in f


def test_score_mentions_align_with_table():
    from app.services.report_content_repairs import fix_score_mentions
    ch = {12: {"markdown": "得分为12.0分。\n\n"
                            "| 序号 | 测评项目 | 得分 |\n|---|---|---|\n"
                            "| 1 | a | 0 |\n| 2 | b | 0 |\n| 合计 | — | 0 |\n"}}
    fix_score_mentions(ch)
    assert "得分为0.0分" in ch[12]["markdown"]


def test_attachment_reference_removed():
    from app.services.report_content_repairs import fix_attachment_ref
    md = "本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。"
    out, n = fix_attachment_ref(md)
    assert n == 1
    assert "详见附件1" not in out


# ── 复垦 ─────────────────────────────────────────────────────
_FILLED = {
    "land_classification": {"rows": [
        {"序号": "1", "一级地类": "耕地", "二级地类": "旱地", "面积_m2": 14000,
         "占比": "52.24", "权属": "宋集村"},
        {"序号": "2", "一级地类": "园地", "二级地类": "果园", "面积_m2": 6000,
         "占比": "22.39", "权属": "宋集村"},
        {"序号": "3", "一级地类": "水域及水利设施用地", "二级地类": "沟渠",
         "面积_m2": 3800, "占比": "14.18", "权属": "宋集村"},
        {"序号": "4", "一级地类": "交通运输用地", "二级地类": "农村道路",
         "面积_m2": 3000, "占比": "11.19", "权属": "宋集村"}]},
    "area_m2": 26800, "care_period_months": 6, "reclaim_period_months": 6,
    "service_months": 30, "land_use_start": "2027-01",
    "_budget_estimate": {"care": 0.6, "monitor": 4.23},
}


def test_reclamation_units_and_headings():
    from app.services.reclamation_content_repairs import fix_units, fix_heading_numbers
    md = "单位：hm\n\n4.1.1 概况\n4.1.3 拟损毁土地现状分析"
    out, n = fix_units(md)
    assert "单位：hm²" in out
    out, n = fix_heading_numbers(out)
    assert "4.1.2 拟损毁土地现状分析" in out


def test_reclamation_service_direction_depth_cost():
    from app.services.reclamation_content_repairs import (
        fix_service_period, fix_restoration, fix_strip_depth, fix_cost_numbers)
    md = ("复垦方向确定为旱地。复垦面积2.0000公顷。剥离厚度25cm。\n"
          "管护时间为半年，费用按照1000元/（人·月）计取，共计21500.00 万元。\n"
          "最终确定方案服务年限为36个月，从2027年1月到2029年12月。\n"
          "2027年2月至2028年6月安排动态投资21500.00 万元。")
    md, _ = fix_service_period(md, _FILLED)
    md, _ = fix_restoration(md, _FILLED)
    md, _ = fix_strip_depth(md, _FILLED)
    md, _ = fix_cost_numbers(md, _FILLED)
    assert "复垦方向为恢复原地类（旱地、果园、沟渠、农村道路）" in md
    assert "复垦面积2.6800公顷" in md
    assert "剥离厚度30cm" in md
    assert "共计 0.60 万元" in md
    assert "服务年限为30个月" in md
    assert "2029年6月" in md
    assert "动态投资4.23 万元" in md
    # 阶段日期不能被服务年限改写
    assert "2027年2月至2028年6月" in md


def test_reclamation_adds_missing_table_3_1():
    from app.services.reclamation_content_repairs import add_table_3_1
    md = "各地类面积见表3-1。"
    out, n = add_table_3_1(md, _FILLED)
    assert n == 1
    assert "表3-1 项目区土地利用现状表" in out
    assert "| 1 | 耕地 | 旱地 | 1.4000 |" in out


def test_reclamation_caption_renumber_only_own_chapter():
    from app.services.reclamation_content_repairs import renumber_captions
    md = "表6-2 复垦使用肥料汇总表\n表7-1 措施费费率表"
    out, n = renumber_captions(md, 6)
    assert "表6-1 复垦使用肥料汇总表" in out
    assert "表7-1 措施费费率表" in out


def test_review_table_labels_not_bold():
    """评审表行标签（决策法律政策依据及必要性/降低风险措施）不再加粗。"""
    from docx import Document
    from app.services.review_table_service import build_review_table_docx
    doc = Document()
    build_review_table_docx(doc, {"project_name": "测试决策", "org_name": "某单位"},
                            {})
    hit = 0
    for t in doc.tables:
        for r in t.rows:
            for c in r.cells:
                txt = c.text.replace("\n", "")
                if "降低风险措施" in txt or "决策法律" in txt:
                    hit += 1
                    for p in c.paragraphs:
                        for run in p.runs:
                            assert not run.bold, (txt, run.text)
    assert hit > 0


def test_truncated_paragraph_is_closed_or_dropped():
    """「话说到一半」的残段：回退到最后一个完整句子，不留下半句话。"""
    from app.services.report_content_repairs import fix_truncated_paragraphs
    md = ("这是一段完整的正文。\n\n"
          "另一段写了很长但是最后用逗号收尾，后面没了，\n\n"
          "| 表格 | 不动 |")
    out, n = fix_truncated_paragraphs(md)
    assert n >= 1
    assert "后面没了" not in out
    assert "| 表格 | 不动 |" in out


def test_llm_trim_incomplete_text():
    from app.services.llm_service import trim_incomplete_text
    t = "前一句完整。后一句说到一半，"
    assert trim_incomplete_text(t) == "前一句完整。"
    assert trim_incomplete_text("公式：Si＝ai+Wi") == "公式：Si＝ai+Wi"


def test_math_symbols_survive_chapter_clean():
    """× ≥ ≤ ㎡ ① 不能被「乱码清洗」删掉（2026-11-11：第12章公式印成 a  b  35）。"""
    from app.agent.agents.chapters.chapter_base import _KEEP_PRINTABLE_RE
    s = "每行得分 = a×b×35；W≥70 为高风险；面积 2.68hm²；①"
    assert _KEEP_PRINTABLE_RE.sub("", s) == s


def test_scrub_does_not_touch_law_inside_book_title():
    """范文地名替换不能把《中华人民共和国村民委员会组织法》换成项目地址。"""
    from app.validation.content_guardrails import scrub_sample_facts
    md = ("4.《中华人民共和国村民委员会组织法》（2018年12月29日修正）；\n"
          "项目位于明远路，面积 1.2000 公顷。")
    sample = "项目位于明远路，面积 1.2000 公顷。《中华人民共和国村民委员会组织法》"
    filled = {"location": "朱坝街道办事处、三圩社区", "area_m2": "326342"}
    out, _n = scrub_sample_facts(md, filled, sample, "")
    assert "中华人民共和国村民委员会组织法" in out
    assert "朱坝街道办事处、三圩社区民委员会" not in out


def test_work_schedule_date_not_inserted_mid_sentence():
    """第7章日期落位不能在「编制社|会稳定」中间插日期。"""
    from app.services.work_schedule import apply_to_chapter7
    md = ("6.编制评估报告。在上述工作基础上，2026年9月28日至2026年9月30日"
          "编制社会稳定风险评估报告，内容包含评估过程。")
    out, _ = apply_to_chapter7(md, {"report_draft_date": "2026-09-28",
                                    "report_draft_end_date": "2026-09-30"})
    assert "编制社会稳定风险评估报告" in out
    assert "编制社　2026年" not in out


def test_use_period_end_date_recomputed():
    """使用期 18 个月的结束月不能抄服务年限的 2029 年 6 月。"""
    from app.services.reclamation_content_repairs import fix_use_period_end
    s = "项目临时用地使用时间为18个月，从2027年1月到2029年6月。"
    out, n = fix_use_period_end(s, {"land_use_months": "18"})
    assert n == 1
    assert "2028年6月" in out and "2029年6月" not in out


def test_review_date_moved_after_report_finished():
    """「报告编制完成」写法下，专家评审日期也要排到编制完成之后。"""
    from app.services.report_content_repairs import fix_date_order
    md = ("本次评估自2026年9月22日接受委托起，至2026年9月30日报告编制完成止。"
          "2026年9月22日至2026年9月30日组织专家评审。")
    out, n = fix_date_order(md)
    assert n == 1
    assert "2026年10月1日至10月9日组织专家评审" in out


def test_risk_overview_follows_ch11_items():
    """表4-1 按第11章的风险清单重建（模板写死六类、第11章实际五类）。"""
    from app.validation.content_guardrails import rebuild_risk_overview_table
    ch11 = ("### （一）决策风险因素分析预测\n\n"
            "**1. 补偿方案风险**\n\n补偿标准存在争议，可能引发不满。\n\n"
            "根据风险因素识别和风险等级判断，该事项风险程度为一般。\n\n"
            "**2. 决策实施风险**\n\n信息公开不及时，可能引发质疑。\n\n"
            "根据风险因素识别和风险等级判断，该事项风险程度为较小。\n\n"
            "**3. 社保名额风险**\n\n名额分配不透明，可能引发争议。\n\n"
            "根据风险因素识别和风险等级判断，该事项风险程度为一般。\n")
    ch4 = ("| 序号 | 风险类型 | 风险因素描述 | 发生阶段 |\n"
           "| --- | --- | --- | --- |\n"
           "| 1 | 征收程序方面的风险 | 程序不规范 | 准备 |\n"
           "| 2 | 补偿方案风险 | 期望值过高 | 准备、实施 |\n"
           "| 3 | 决策实施风险 | 信息不透明 | 准备、实施 |\n"
           "| 4 | 社保名额风险 | 分配未明确 | 准备、实施 |\n"
           "| 5 | 资金风险 | 资金不足 | 准备、实施 |\n"
           "| 6 | 水系影响风险 | 影响防洪 | 准备、实施 |\n\n"
           "从上表可见，本决策主要风险集中在程序、补偿、实施、社保、资金和水系六个方面。\n")
    chapters = {4: {"markdown": ch4}, 11: {"markdown": ch11}}
    assert rebuild_risk_overview_table(chapters) == 1
    out = chapters[4]["markdown"]
    assert "征收程序方面的风险" not in out
    assert "| 1 | 补偿方案风险 | 期望值过高 | 准备、实施 |" in out
    assert "五个方面" not in out and "三个方面" in out


def test_risk_measures_table_follows_ch11():
    """表13-1 的风险类型列与行序按第11章重排：多余的删除、缺的补上。"""
    from app.validation.content_guardrails import rebuild_risk_measures_table
    ch11 = ("### （一）分析\n\n"
            "**1. 补偿方案风险**\n\n正文。风险程度为一般。\n\n"
            "**2. 资金风险**\n\n正文。风险程度为一般。\n\n"
            "**3. 舆情风险**\n\n正文。风险程度为较小。\n")
    ch13 = ("### （一）资金保障\n\n落实专户存储、专款专用。\n\n"
            "### （二）舆情应对\n\n及时核实并公开回应涉项目舆情。\n\n"
            "| 序号 | 风险类型 | 防范化解措施 | 责任主体 | 完成时限 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 1 | 征收程序方面的风险 | 依法履行程序 | 区政府 | 批准前 |\n"
            "| 2 | 补偿方案风险 | 按区片价测算 | 区政府 | 公告前 |\n"
            "| 3 | 资金风险 | 专户存储 | 区政府 | 批准前 |\n")
    chapters = {11: {"markdown": ch11}, 13: {"markdown": ch13}}
    assert rebuild_risk_measures_table(chapters) == 1
    out = chapters[13]["markdown"]
    assert "征收程序方面的风险" not in out
    assert "| 3 | 舆情风险 |" in out and "| 2 | 资金风险 | 专户存储" in out
