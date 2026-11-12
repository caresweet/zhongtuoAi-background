# -*- coding: utf-8 -*-
"""复垦成稿按 2026-11-12 专家批注（城东污水厂）的回归用例。

批注原文 → 断言：
    0  送审时间 2027-01-10「最好2027.1月前」
    1  使用时间 18 个月、区间却写到 2029-06「30个月，不是18月」
    2  「见表4-2」→ 表4-1
    3  「见表4-6」→ 表4-3
    4  「各评价单元复垦方向如下表所示」下面没表「缺少表」
    5  「耕地和林地」→ 本项目无林地
    6  表4-14 面积统计表「空白」
    7/8/9 「耕地」→ 覆盖不全
    10 「见表7-2」但缺其他费用表「缺少表」
    11 管护期 0.60 与表8-1 的 1.26「和下表中数据不对呀」
    12/13 沟渠/农村道路复垦面积抄成总面积
    14 第8章使用期/服务年限「不对应」
"""
from app.services.reclamation_content_repairs import (
    add_other_cost_table,
    fill_area_table,
    fix_dangling_table_phrases,
    fix_phase_schedule,
    fix_restoration,
    fix_submit_date,
    fix_table_refs,
    fix_topsoil_land_types,
    fix_use_period_end,
    repair_reclamation_chapters,
)

_LC = {"rows": [
    {"序号": "1", "一级地类": "耕地", "二级地类": "旱地", "面积_m2": 14000},
    {"序号": "2", "一级地类": "园地", "二级地类": "果园", "面积_m2": 6000},
    {"序号": "3", "一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 3800},
    {"序号": "4", "一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 3000},
]}

_FILLED = {
    "land_classification": _LC,
    "area_m2": 26800,
    "land_use_period_months": 18,
    "land_use_start": "2027-01",
    "land_use_end": "2028-06",
    "reclaim_period_months": 6,
    "care_period_months": 6,
    "service_months": 30,
    "topsoil_thickness": "30",
    "submit_date": "2027-01-10",
    "_budget_estimate": {
        "construction": 24.51, "equipment": 0.0, "other": 3.97,
        "monitor_care": 20.25, "monitor": 0.37, "care": 0.6,
        "acceptance": 19.28, "reserve": 8.75, "basic_reserve": 2.92,
        "price_reserve": 5.34, "risk": 0.49,
        "static_total": 52.14, "dynamic_total": 57.48,
        "items": [{"name": "表土剥离 — 74kW III类土",
                   "section": "土壤剥离工程", "amount_wan": 3.52164}],
    },
}


def test_use_period_end_is_use_months_not_service_months():
    """批注 1/14：18 个月用地区间是 2027-01～2028-06，不是服务年限的 2029-06。"""
    md = "本项目临时用地使用时间为18个月，从2027年1月到2029年6月。"
    out, n = fix_use_period_end(md, _FILLED)
    assert n == 1
    assert "从2027年1月到2028年6月" in out


def test_table_refs_retargeted_to_actual_captions():
    """批注 2/3：表号按本章实际表题重定向。"""
    md = ("## 4.1 土地损毁分析与评定\n本项目临时用地拟损毁情况见表4-2。\n\n"
          "表4-1 临时用地拟损毁情况统计表\n\n| 序号 | 地块名称 |\n|---|---|\n| 1 | 施工场站 |\n\n"
          "## 4.2 复垦区土地利用状况\n"
          "本项目临时用地范围内各地类的面积及占总面积比例见表4-6。\n\n"
          "表4-3 临时用地土地利用现状统计表\n\n| 地类 | 面积 |\n|---|---|\n| 旱地 | 1.4 |\n")
    out, n = fix_table_refs(md, 4)
    assert n == 2
    assert "拟损毁情况见表4-1" in out
    assert "占总面积比例见表4-3" in out


def test_table_refs_resolve_to_system_table_numbers():
    """第4章系统表不在 markdown 里，引用按句子话题落到系统表最终编号。"""
    md = ("## 4.1 土地损毁分析与评定\n本项目临时用地拟损毁情况见表4-2。\n\n"
          "## 4.2 复垦区土地利用状况\n各地类的面积及占总面积比例见表4-6。\n")
    known = ("表4-1 临时用地拟损毁情况统计表",
             "表4-3 临时用地土地利用现状统计表")
    out, n = fix_table_refs(md, 4, known)
    assert n == 2
    assert "拟损毁情况见表4-1" in out
    assert "占总面积比例见表4-3" in out


def test_dangling_table_phrase_points_to_existing_table():
    """批注 4：「如下表所示」后面没有表 → 指向本章前面的表。"""
    md = ("表4-11 评价单元复垦方向表\n\n| 序号 | 评价单元 |\n|---|---|\n| 1 | 施工场站 |\n\n"
          "### 4.4.3 复垦方向的确定\n"
          "复垦后各类用地恢复原状，面积保持不变。各评价单元复垦方向如下表所示。\n")
    out, n = fix_dangling_table_phrases(md, 4)
    assert n == 1
    assert "各评价单元复垦方向见表4-11" in out


def test_empty_area_table_is_filled():
    """批注 6：表4-14 只有表题、表身空白 → 按土地分类面积补表。"""
    md = "## 4.6 复垦的目标任务\n表4-14 面积统计表\n\n复垦责任范围内拟复垦土地面积2.6800hm²。\n"
    out, n = fill_area_table(md, _FILLED, 4)
    assert n == 1
    assert "| 合计 | 2.6800 | 2.6800 | 0.0000 |" in out
    assert "| 旱地 | 1.4000 | 1.4000 | 0.0000 |" in out


def test_other_cost_table_added_and_investment_table_renumbered():
    """批注 10：7.2.3 指向的表不存在 → 补「其他费用估算表」。"""
    md = ("### 7.2.3 其他费用\n"
          "其他费用由前期工作费、工程监理费、拆迁补偿费、竣工验收费、业主管理费等组成，"
          "计算结果见表7-2。\n\n"
          "### 7.2.6 技术经济指标\n表7-2 土地复垦投资估算表\n\n"
          "| 序号 | 名称 | 金额 |\n|---|---|---|\n| 一 | 工程施工费 | 24.51 |\n")
    out, n = add_other_cost_table(md, _FILLED)
    assert n == 1
    assert "表7-2 其他费用估算表" in out
    assert "| 合计 | — | — | 3.97 |" in out


def test_other_cost_table_fills_bold_empty_caption():
    """模型写了 `**表7-4 其他费用估算表**` 但没表身 → 就地补表，不另起表题。"""
    md = ("#### 7.2.3 其他费用\n\n其他费用由前期工作费、工程监理费等组成，计算结果见表7-4。\n\n"
          "**表7-4 其他费用估算表**\n\n#### 7.2.4 监测管护费\n")
    out, n = add_other_cost_table(md, _FILLED)
    assert n == 1
    assert out.count("其他费用估算表") == 1   # 表题只有一处，不另起
    assert "| 前期工作费 |" in out
    assert "| 合计 | — | — | 3.97 |" in out


def test_phase_schedule_matches_ch7_care_fee():
    """批注 11：第8章管护期 0.60（7.2.4 口径），四期合计=动态总投资。"""
    md = ("## 8.2 土地复垦费用安排\n"
          "表土剥离期安排费用7.62万元。用地监测期安排费用20.25万元。"
          "复垦施工期安排费用28.35万元。管护期安排费用1.26万元，用于管护。"
          "各阶段费用合计57.48万元，与动态总投资一致。\n\n"
          "表8-1 统计表\n\n| 复垦时间 | 动态总投资（万元） | 复垦工作 |\n"
          "|---|---|---|\n| 2027年1月 | 7.62 | 表土剥离 |\n")
    out, n = fix_phase_schedule(md, _FILLED, 8)
    assert n >= 5
    assert "管护期安排费用0.60万元" in out
    assert "| 2029年1月至2029年6月 | 0.60 | 管护期田间管理、沟渠清淤 |" in out
    assert "| 合计 | 57.48 | — |" in out
    assert "表8-1 土地复垦费用安排表" in out


def test_per_class_restoration_areas():
    """批注 12/13：沟渠 0.38、农村道路 0.30，不是总面积 2.68。"""
    md = ("其中旱地复垦面积1.4000公顷，园地复垦面积0.6000公顷。"
          "沟渠复垦面积2.6800公顷。农村道路复垦面积2.6800公顷。")
    out, n = fix_restoration(md, _FILLED)
    assert n >= 2
    assert "沟渠复垦面积0.3800公顷" in out
    assert "农村道路复垦面积0.3000公顷" in out


def test_dangling_table_phrase_points_to_system_table_caption():
    """系统表已从 markdown 里删掉时，靠装配器传入的最终表题指回去。"""
    md = "### 4.4.3 复垦方向的确定\n各评价单元复垦方向如下表所示。\n"
    out, n = fix_dangling_table_phrases(md, 4, ("表4-10 评价单元复垦方向表",))
    assert n == 1
    assert "各评价单元复垦方向见表4-10" in out


def test_land_wording_follows_project_land_types():
    """批注 5/7/8/9：地类名称按**本项目土地分类面积**取，不能一律写「耕地和园地」。"""
    md = ("需剥离表土的地类主要为耕地和林地。"
          "表土剥离主要针对现状的耕地，面积为2.0000hm²。"
          "复垦后耕地面积为2.0000hm²。"
          "土地翻耕与田面平整面积均为2.0000hm²，与复垦后旱地面积对应。")
    out, _ = fix_topsoil_land_types(md, _FILLED)
    assert "耕地和林地" not in out
    assert "耕地和园地" in out
    # 原句是二级地类口径（旱地）→ 补的是「旱地和果园」，不是「耕地和园地」
    assert "与复垦后旱地和果园面积对应" in out


def test_land_wording_keeps_single_class_project_unchanged():
    """只有一个旱地地类时**不得**改写成「耕地和园地」（业主 2026-11-12 明确）。"""
    md = "表土剥离主要针对现状的旱地，面积为2.0000hm²。复垦后旱地面积为2.0000hm²。"
    filled = {"land_classification": {"rows": [
        {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 20000}]}}
    out, _ = fix_topsoil_land_types(md, filled)
    assert "耕地和园地" not in out
    assert "旱地" in out


def test_submit_date_moved_before_land_use_start():
    """批注 0：送审时间应在用地开始（2027-01）之前。"""
    filled = dict(_FILLED)
    assert fix_submit_date(filled) == 1
    assert filled["submit_date"].startswith("2026-12")


def test_area_table_renumbered_when_system_table_takes_its_number():
    """表4-14 已被系统「结构对比表」占用 → 面积统计表顺延到 4-15，不重号。"""
    md = "表4-14 面积统计表\n\n拟复垦土地面积2.6800hm²。\n"
    out, n = fill_area_table(md, _FILLED, 4, ("表4-14 临时用地复垦前复垦后结构对比表",))
    assert n == 1
    assert "表4-15 面积统计表" in out


def test_empty_area_table_with_bare_caption_is_filled():
    """裸表题「表4-14」（装配器事后补名）也要补表身 —— 批注 6 的空表就是这个形态。"""
    md = ("## 4.7 复垦的目标任务\n本项目拟复垦土地面积2.6800hm²。"
          "具体复垦前复垦后面积见下表。\n\n单位：hm²\n\n表4-14\n\n"
          "本项目拟复垦土地面积以复垦前土地利用现状为基础。\n")
    out, n = fill_area_table(md, _FILLED, 4)
    assert n == 1
    assert "表4-14 面积统计表" in out
    assert "| 合计 | 2.6800 | 2.6800 | 0.0000 |" in out


def test_empty_area_table_with_bare_caption_cited_by_number():
    """引出句写「面积见表4-14」而不是「见下表」时，空表同样要补。"""
    md = ("## 4.6 复垦的目标任务\n本项目拟复垦土地面积2.6800hm²，"
          "具体复垦前复垦后面积见表4-14。\n\n单位：hm²\n\n表4-14\n\n"
          "复垦责任范围涉及旱地1.4000hm²。\n")
    out, n = fill_area_table(md, _FILLED, 4)
    assert n == 1
    assert "表4-14 面积统计表" in out
    assert "| 合计 | 2.6800 | 2.6800 | 0.0000 |" in out


def test_empty_area_table_with_header_only_is_filled():
    """只有表头的空表（渲染后被丢掉）同样要补数据行。"""
    md = ("表4-14 面积统计表\n\n"
          "| 地类 | 复垦前面积（公顷） | 复垦后面积（公顷） | 增减（公顷） |\n"
          "|---|---|---|---|\n")
    out, n = fill_area_table(md, _FILLED, 4)
    assert n == 1
    assert "| 合计 | 2.6800 | 2.6800 | 0.0000 |" in out


def test_water_table_restoration_direction_is_deduped_by_class():
    """表4-12 复垦方向栏：分区方向按类别去重（原写法拼出「旱地、果园、旱地、果园…」）。"""
    from app.services.reclamation_ch4_tables import table_4_12
    filled = dict(_FILLED)
    filled["_land_use_zones"] = [
        {"name": "施工场站", "area_m2": 9800, "reclaim_direction": "旱地、果园"},
        {"name": "管道开挖带", "area_m2": 9200, "reclaim_direction": "旱地、果园、沟渠"},
        {"name": "施工便道", "area_m2": 4800, "reclaim_direction": "旱地、农村道路"},
    ]
    t = table_4_12(filled)
    direction = t["rows"][0][3]
    assert direction == "旱地、果园、沟渠、农村道路"


def test_table_4_11_lead_uses_actual_land_classes():
    """表4-11 引导语「需剥离表土的地类」按本项目实际地类生成（批注 5）。"""
    from app.services.reclamation_ch4_tables import build_ch4_tables
    tables = dict((t["caption"], t.get("lead") or "")
                  for _s, t in build_ch4_tables(_FILLED, title="x", plan=None))
    lead = next(v for k, v in tables.items() if "表土资源平衡" in k)
    assert "耕地和园地" in lead
    assert "耕地和林地" not in lead
    # 只有旱地的项目 → 写「旱地」，不能写成「耕地和园地」
    one = {"topsoil_thickness": "30", "land_classification": {"rows": [
        {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 20000}]}}
    tables2 = dict((t["caption"], t.get("lead") or "")
                   for _s, t in build_ch4_tables(one, title="x", plan=None))
    lead2 = next((v for k, v in tables2.items() if "表土资源平衡" in k), "")
    assert "旱地" in lead2 and "耕地和园地" not in lead2


def test_caption_order_and_refs_follow_document_position():
    """业主 2026-11-12：上文「见表x-x」→ 下文那张表就是 x-x；编号按出现顺序不跳号。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler
    doc = Document()
    for t in ("4 土地复垦方向可行性分析",
              "本项目临时用地拟损毁情况见表4-2。",
              "表4-1 临时用地拟损毁情况统计表",
              "本项目临时用地范围内各地类的面积及占总面积比例见表4-6。",
              "表4-3 临时用地土地利用现状统计表",
              "各评价单元复垦方向如下表所示。",
              "表4-9 评价单元复垦方向表"):
        doc.add_paragraph(t)
    a = ReportAssembler()
    a._domain = "reclamation"
    a._normalize_caption_order(doc)
    texts = [p.text for p in doc.paragraphs]
    caps = [t for t in texts if t.startswith("表")]
    refs = [t for t in texts if "见" in t]
    assert caps == ["表4-1 临时用地拟损毁情况统计表",
                    "表4-2 临时用地土地利用现状统计表",
                    "表4-3 评价单元复垦方向表"]
    assert "拟损毁情况见表4-1" in refs[0]
    assert "占总面积比例见表4-2" in refs[1]
    assert "复垦方向见表4-3" in refs[2]


def test_caption_refs_prefer_table_with_matching_topic():
    """「见表4-6」但讲的是土地利用现状 → 指到土地利用现状那张表，不是下一张。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler
    doc = Document()
    for t in ("4 土地复垦方向可行性分析",
              "表4-3 临时用地土地利用现状统计表",
              "项目区临时用地土地利用现状统计见表4-6。",
              "表4-6 土地权属状况表",
              "各评价单元复垦方向见表4-12：",
              "表4-11 评价单元复垦方向表",
              "表4-12 临时用地表土资源平衡分析表"):
        doc.add_paragraph(t)
    a = ReportAssembler()
    a._domain = "reclamation"
    a._normalize_caption_order(doc)
    texts = [p.text for p in doc.paragraphs]
    assert any("土地利用现状统计见表4-1" in t for t in texts)
    assert any("各评价单元复垦方向见表4-3" in t for t in texts)


def test_total_row_with_dash_cells_is_not_split(tmp_path):
    """`| 合计 | — | — | 3.97 |` 不能被「单行多表」拆分逻辑拆成两行（合计金额串列）。"""
    import docx
    from app.services.report_assembler import ReportAssembler
    md = ("# 7 土地复垦投资估算\n\n## 7.2 估算成果\n\n### 7.2.3 其他费用\n\n"
          "计算结果见表7-2。\n\n表7-2 其他费用估算表\n\n"
          "| 序号 | 费用名称 | 计算式 | 金额（万元） |\n|---|---|---|---|\n"
          "| 1 | 前期工作费 | 工程施工费×5.6% | 1.37 |\n"
          "| 合计 | — | — | 3.97 |\n")
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": "reclamation",
                      "chapters": {7: {"markdown": md}},
                      "filled_data": {"project_name": "X"}})
    d = docx.Document(str(tmp_path / "generated" / out.split("/")[-1]))
    for t in d.tables:
        if "计算式" in " ".join(c.text for c in t.rows[0].cells):
            assert [c.text for c in t.rows[-1].cells] == ["合计", "—", "—", "3.97"]
            break
    else:
        raise AssertionError("其他费用表没渲染出来")


def test_land_table_total_row_missing_second_cell(tmp_path):
    """地类表合计行少写一格（`| 合计 | 5.4000 | 100 |`）→ 补「合计」，面积不串列。"""
    import docx
    from app.services.report_assembler import ReportAssembler
    md = ("# 4 土地复垦方向可行性分析\n\n## 4.2 复垦区土地利用状况\n\n"
          "表4-5 主体项目用地土地利用现状统计表\n\n"
          "| 地类名称及编码 | 地类名称及编码 | 面积（hm²） | 占比（%） |\n"
          "|---|---|---|---|\n"
          "| 建设用地 | 建设用地 | 5.4000 | 100 |\n"
          "| 合计 | 5.4000 | 100 |\n")
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": "reclamation",
                      "chapters": {4: {"markdown": md}},
                      "filled_data": {"project_name": "X"}})
    d = docx.Document(str(tmp_path / "generated" / out.split("/")[-1]))
    for t in d.tables:
        if "地类名称及编码" in " ".join(c.text for c in t.rows[0].cells):
            last = [c.text.strip() for c in t.rows[-1].cells]
            assert last == ["合计", "合计", "5.4000", "100"], last
            break


def test_malformed_three_level_table_ref_is_dropped():
    """跨章「见表4-24-2」这种三级畸形表号 → 整句删掉（业主：不许出现对不上的表号）。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler
    doc = Document()
    for t in ("1 前言", "具体损毁情况统计表及临时用地分区见表4-24-2所示。",
              "4 土地复垦方向可行性分析", "表4-1 临时用地拟损毁情况统计表"):
        doc.add_paragraph(t)
    a = ReportAssembler()
    a._domain = "reclamation"
    a._normalize_caption_order(doc)
    texts = [p.text for p in doc.paragraphs]
    assert not any("表4-24-2" in t for t in texts)


def test_stability_cross_chapter_table_ref_uses_old_to_new_mapping():
    """稳评共用同一套编号逻辑：第五章「见表6-3」要跟第六章重排后的 6-2 对上。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler
    doc = Document()
    for t in ("第五章 风险分析", "本项目主要风险因素详见表6-3。",
              "第六章 项目概况", "表6-1 项目基本情况表", "表6-3 土地利用现状表"):
        doc.add_paragraph(t)
    a = ReportAssembler()
    a._domain = "stability"
    a._normalize_caption_order(doc)
    texts = [p.text for p in doc.paragraphs]
    assert "本项目主要风险因素详见表6-2。" in texts
    assert "表6-2 土地利用现状表" in texts


def test_risk_count_consistent_across_stability_chapters():
    """上文 5 个风险点，后文不能写 6 个 / 六项（业主 2026-11-12：上下文必须统一）。"""
    from app.services.report_content_repairs import fix_risk_count
    ch11 = "\n\n".join(
        f"**{i}. 第{i}类事项方面的风险**\n\n根据该事项风险因素的识别和风险等级的判断，"
        f"该事项风险程度为{'较小' if i == 5 else '一般'}。" for i in range(1, 6))
    chapters = {
        11: {"markdown": ch11 + "\n\n本决策共5个方面主要风险因素。"},
        12: {"markdown": "从上表可见，本项目共识别6项主要风险因素，"
                         "初始评估中各项风险程度均为一般。"},
        13: {"markdown": "上表所列措施与第11章识别的六项风险一一对应。"},
    }
    fix_risk_count(chapters)
    assert "共5项主要风险因素" in chapters[12]["markdown"]
    assert "4项为一般风险、1项为较小风险" in chapters[12]["markdown"]
    assert "第11章识别的5项风险" in chapters[13]["markdown"]


def test_reclamation_zone_plot_counts_match_table():
    """表4-1 写管道开挖带 3 个地块、表土堆放区 2 个，正文不能都写 1 个。"""
    from app.services.reclamation_content_repairs import fix_zone_plot_counts
    filled = {"_land_use_zones": [
        {"name": "施工场站", "area_m2": 9800, "plot_count": 2},
        {"name": "管道开挖带", "area_m2": 9200, "plot_count": 3},
        {"name": "施工便道", "area_m2": 4800, "plot_count": 1},
        {"name": "表土堆放区", "area_m2": 3000, "plot_count": 2},
    ]}
    md = ("施工场站2个地块。管道开挖带1个地块。"
          "施工便道为1个地块。表土堆放区1个地块。涉及4个地块。"
          "本项目涉及多个地块。")
    out, n = fix_zone_plot_counts(md, filled)
    assert n >= 3
    assert "管道开挖带3个地块" in out
    assert "表土堆放区2个地块" in out
    assert "施工场站2个地块" in out
    assert "涉及8个地块" in out


def test_two_level_land_table_total_keeps_alignment(tmp_path):
    """表头是「地类名称及编码 | 二级地类」时，合计行少一格要补「—」，面积不能串列。"""
    import docx
    from app.services.report_assembler import ReportAssembler
    md = ("# 4 土地复垦方向可行性分析\n\n## 4.2 复垦区土地利用状况\n\n"
          "表4-5 主体项目用地土地利用现状统计表\n\n"
          "| 一级地类 | 二级地类 | 面积（hm²） | 占比（%） |\n"
          "|---|---|---|---|\n"
          "| 工业仓储用地（06） | 工业用地（0601） | 5.4000 | 100.00 |\n"
          "| 合计 | 5.4000 | 100.00 |\n")
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": "reclamation",
                      "chapters": {4: {"markdown": md}},
                      "filled_data": {"project_name": "X"}})
    d = docx.Document(str(tmp_path / "generated" / out.split("/")[-1]))
    for t in d.tables:
        if "二级地类" in " ".join(c.text for c in t.rows[0].cells):
            last = [c.text.strip() for c in t.rows[-1].cells]
            assert last == ["合计", "—", "5.4000", "100.00"], last
            break


def test_non_jian_table_ref_uses_sentence_topic():
    """「依据表4-9 的分级标准」——分级标准是表4-6 指标体系，不能留在 4-9。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler
    doc = Document()
    for t in ("4 土地复垦方向可行性分析",
              "表4-6 土地适宜性评价指标体系",
              "表4-7 土地适宜性分值",
              "依据表4-9 的分级标准，结合各评价单元复垦后的实际条件逐项赋分，见表4-7。"):
        doc.add_paragraph(t)
    a = ReportAssembler()
    a._domain = "reclamation"
    a._normalize_caption_order(doc)
    texts = [p.text for p in doc.paragraphs]
    assert any("依据表4-1 的分级标准" in t for t in texts)


def test_missing_area_table_filled_at_assembly(tmp_path):
    """表题下真的没有表（模板/裸表题来源）—— 成稿层兜底补一张。"""
    import docx
    from docx.oxml.ns import qn
    from app.services.report_assembler import ReportAssembler
    md = ("# 4 土地复垦方向可行性分析\n\n## 4.6 复垦的目标任务\n\n"
          "具体复垦前复垦后面积见表4-14。\n\n表4-14 面积统计表\n\n后续说明文字。\n")
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble({"session_id": "t", "_domain": "reclamation",
                      "chapters": {4: {"markdown": md}}, "filled_data": _FILLED})
    d = docx.Document(str(tmp_path / "generated" / out.split("/")[-1]))
    p = next(p for p in d.paragraphs if "面积统计表" in p.text)
    nxt = p._element.getnext()
    while nxt is not None and nxt.tag == qn('w:p') and not "".join(nxt.itertext()).strip():
        nxt = nxt.getnext()
    assert nxt is not None and nxt.tag == qn('w:tbl')
    from docx.table import Table
    tb = Table(nxt, d)
    assert "增减" in " ".join(c.text for c in tb.rows[0].cells)


def test_repair_keeps_trailing_newline():
    """装配器靠行尾空行触发表题 flush —— 修补不能把末尾换行吃掉。"""
    md = "## 6.2 工程量测算\n\n各项工程量见表6-1。\n\n" \
         "表6-1 项目复垦工程量汇总表\n\n| 序号 | 名称 |\n|---|---|\n| 1 | 表土剥离 |\n"
    chapters = {6: {"markdown": md}}
    repair_reclamation_chapters(chapters, {"project_name": "X"})
    assert chapters[6]["markdown"].endswith("\n")
