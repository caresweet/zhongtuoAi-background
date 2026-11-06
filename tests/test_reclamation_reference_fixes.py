# -*- coding: utf-8 -*-
"""复垦参照件渲染的两个修补（2026-11-03 对照人工稿 11.2 实测）。

1. 第 7 章费用占位符 `{{cost_*}}` / `{{total_*}}` 取不到值 → 整句被删，
   成稿缺 7.1.3 整节、7.2.4 缺监测费、7.2.5 缺三项金额。
2. 第 5 章 5.3.3「永久基本农田复垦措施」是范文的 0.1917 hm²，照搬后与
   4.2.2「不涉及永久基本农田」正面冲突。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reference_corpus import (      # noqa: E402
    _budget_slot, _drop_unused_basic_farmland, resolve_slot_value,
)


_BUDGET = {
    "construction": 89.54, "equipment": 0.0, "other": 14.48,
    "monitor_care": 1.94, "monitor": 1.34, "care": 0.6, "acceptance": 0.0,
    "reserve": 19.85, "basic_reserve": 6.35, "price_reserve": 11.8, "risk": 1.79,
    "static_total": 114.11, "dynamic_total": 125.81,
}


def test_budget_slots_resolve_from_estimate():
    filled = {"_budget_estimate": _BUDGET, "area_mu": 34.87}
    assert _budget_slot("total_static", filled) == "114.11"
    assert _budget_slot("total_dynamic", filled) == "125.81"
    assert _budget_slot("cost_construction", filled) == "89.54"
    assert _budget_slot("cost_monitor_manage", filled) == "1.94"
    assert _budget_slot("cost_monitor", filled) == "1.34"
    assert _budget_slot("cost_risk", filled) == "1.79"
    assert _budget_slot("unit_static", filled) == "3.27"


def test_budget_slot_missing_when_no_estimate():
    assert _budget_slot("total_static", {}) == ""


def test_resolve_slot_returns_ok_for_cost_slots():
    v, status = resolve_slot_value("cost_construction",
                                   {"_budget_estimate": _BUDGET})
    assert status == "ok" and v == "89.54"


def test_unit_investment_uses_area_m2_fallback():
    v = _budget_slot("unit_static",
                     {"_budget_estimate": _BUDGET, "area_m2": 23248})
    assert v == "3.27"


_MD5 = """## 5.3 复垦措施

### 5.3.2 生物化学措施

施用有机肥。

### 5.3.3 永久基本农田复垦措施

本工程临时用地共占用永久基本农田2.3248 hm2，规划方向为恢复原状。

### 5.4 监测措施

设置监测点。
"""


def test_drop_basic_farmland_section_when_not_occupied():
    out = _drop_unused_basic_farmland(_MD5, {"location": "淮安市淮安区"})
    assert "5.3.3" not in out
    assert "永久基本农田复垦措施" not in out
    assert "### 5.4 监测措施" in out


def test_keep_basic_farmland_section_when_area_given():
    out = _drop_unused_basic_farmland(_MD5, {"basic_farmland_ha": 0.1917})
    assert "5.3.3 永久基本农田复垦措施" in out


def test_ensure_section_headings_inserts_missing_h2():
    from app.validation.content_guardrails import ensure_section_headings
    md = ("## 4.4 土地复垦适宜性评价\n\n### 4.4.4 复垦方向的确定\n\n正文。\n\n"
          "### 4.5.1 土资源平衡分析\n\n正文。\n\n### 4.5.2 水资源平衡分析\n\n正文。")
    out, n = ensure_section_headings(md, 4, "reclamation")
    assert n == 1
    assert "## 4.5 水土资源平衡分析" in out
    assert out.index("## 4.5 水土资源平衡分析") < out.index("### 4.5.1")


def test_ensure_section_headings_leaves_complete_doc_untouched():
    from app.validation.content_guardrails import ensure_section_headings
    md = "## 4.5 水土资源平衡分析\n\n### 4.5.1 土资源平衡分析\n\n正文。"
    out, n = ensure_section_headings(md, 4, "reclamation")
    assert n == 0 and out == md


def test_ensure_section_headings_ignores_other_domains():
    from app.validation.content_guardrails import ensure_section_headings
    md = "### 4.5.1 土资源平衡分析\n\n正文。"
    out, n = ensure_section_headings(md, 4, "stability")
    assert n == 0 and out == md


def _filled_land():
    return {"land_classification": {"合计_m2": 23248, "rows": [
        {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 22215},
        {"一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 417},
        {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 616},
    ]}}


def _qty(rows, name):
    return next(r["qty"] for r in rows if r["name"] == name)


def test_quantity_rows_omit_strip_without_thickness():
    """没填厚度就不出「表土剥离」——不拿任何范文的 0.2/0.3 顶替。"""
    from app.services.reclamation_quantities import build_quantity_rows
    rows = build_quantity_rows(_filled_land())
    assert "表土剥离" not in [r["name"] for r in rows]
    assert "表土回填" not in [r["name"] for r in rows]


def test_quantity_rows_honor_user_thickness_cm():
    from app.services.reclamation_quantities import build_quantity_rows
    f = _filled_land(); f["topsoil_thickness"] = "30"
    rows = build_quantity_rows(f)
    assert abs(_qty(rows, "表土剥离") - 66.645) < 0.01     # 22215 × 0.30 m


def test_quantity_rows_accept_thickness_in_meters():
    from app.services.reclamation_quantities import build_quantity_rows
    f = _filled_land(); f["topsoil_thickness"] = "0.3"
    rows = build_quantity_rows(f)
    assert abs(_qty(rows, "表土剥离") - 66.645) < 0.01


def test_no_cleanup_items_without_surface_design():
    """没填结构做法/厚度就不出清理工程（不默认碎石、也不默认混凝土）。"""
    from app.services.reclamation_quantities import build_quantity_rows
    names = [r["name"] for r in build_quantity_rows(_filled_land())]
    assert "垫层清理" not in names and "混凝土面层拆除" not in names
    assert "垃圾外运" not in names


def test_gravel_surface_uses_project_thickness():
    from app.services.reclamation_quantities import build_quantity_rows
    f = _filled_land()
    f.update({"hard_surface_type": "碎石垫层+钢板", "surface_thickness_cm": "15",
              "hard_area_m2": 23248})
    rows = build_quantity_rows(f)
    assert "混凝土面层拆除" not in [r["name"] for r in rows]
    assert abs(_qty(rows, "垫层清理") - 34.872) < 0.01     # 23248 × 0.15 m
    assert abs(_qty(rows, "垃圾外运") - 34.872) < 0.01


def test_concrete_surface_produces_demolition_items():
    from app.services.reclamation_quantities import build_quantity_rows
    f = _filled_land()
    f.update({"hard_surface_type": "混凝土面层+垫层", "hard_area_m2": 23248,
              "surface_thickness_cm": "10", "cushion_thickness_cm": "8"})
    rows = build_quantity_rows(f)
    # 面层 0.10m、垫层 0.08m（用户给的项目设计值）
    assert abs(_qty(rows, "混凝土面层拆除") - 23.248) < 0.01
    assert abs(_qty(rows, "垫层清除") - 18.5984) < 0.01
    assert abs(_qty(rows, "垃圾外运") - 41.8464) < 0.01


def test_hard_area_override_matches_manual():
    """安井组人工稿：硬化 2.0048 ha，垫层清理 3007.2 m³。"""
    from app.services.reclamation_quantities import build_quantity_rows
    f = _filled_land()
    f.update({"hard_area_m2": 20048, "hard_surface_type": "碎石垫层+钢板",
              "surface_thickness_cm": "15"})
    rows = build_quantity_rows(f)
    assert abs(_qty(rows, "垫层清理") - 30.072) < 0.01     # 20048 × 0.15 m


def test_leveling_uses_cultivated_area_not_total():
    from app.services.reclamation_quantities import build_quantity_rows
    rows = build_quantity_rows(_filled_land())
    assert abs(_qty(rows, "田面平整") - 222.15) < 0.01     # 22215 ㎡，不是 23248


def test_drop_duplicate_sections_removes_second_block():
    """同一章里重复的小节（标题+正文）只保留第一次出现。"""
    from docx import Document
    from app.services.report_assembler import ReportAssembler

    d = Document()
    d.add_heading("4 土地复垦方向可行性分析", 1)
    d.add_heading("4.1 土地损毁分析与评定", 2)
    d.add_paragraph("第一段")
    d.add_heading("4.2 复垦区土地利用状况", 2)
    d.add_paragraph("第二段")
    # 模型重写一遍
    d.add_heading("4.1 土地损毁分析与评定", 2)
    d.add_paragraph("重复段")
    d.add_heading("4.1.1 土地损毁环节与时序", 3)
    d.add_paragraph("重复子段")
    d.add_heading("4.3 生态环境影响分析", 2)
    d.add_paragraph("第三段")

    ReportAssembler()._drop_duplicate_sections(d)
    texts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    assert texts.count("4.1 土地损毁分析与评定") == 1
    assert "重复段" not in texts and "重复子段" not in texts
    assert "4.2 复垦区土地利用状况" in texts
    assert "4.3 生态环境影响分析" in texts and "第三段" in texts


def test_ensure_section_headings_accepts_shifted_numeric_level():
    """复垦参照件里 `### 5.1` 已存在（整体下移一格），不能再插一条 `## 5.1`。"""
    from app.validation.content_guardrails import ensure_section_headings
    md = ("### 5.1 土地复垦质量要求\n\n#### 5.1.1 清理工程标准\n\n正文。\n\n"
          "### 5.2 预防控制措施\n\n正文。")
    out, n = ensure_section_headings(md, 5, "reclamation")
    assert n == 0 and out == md


def test_table_4_9_scores_are_differentiated_and_weighted():
    """表4-9 不再全是 90 分：按损毁程度折减，重度区低于中度区，分值可复核。"""
    from app.services.reclamation_ch4_tables import table_4_9
    t = table_4_9({"area_m2": "23248", "land_use_zones":
                   "井场 0.2310公顷；施工场站 1.1950公顷；"
                   "施工便道 0.5788公顷；表土堆放区 0.3200公顷"})
    assert t["caption"] == "表4-9 土地适宜性分值"
    rows = {r[0]: r for r in t["rows"]}
    assert set(rows) == {"井场", "施工场站", "施工便道", "表土堆放区"}
    # 因子分不是一刀切 90
    assert len({rows["井场"][i] for i in range(1, 11)}) >= 4
    heavy = float(rows["井场"][-2])
    medium = float(rows["表土堆放区"][-2])
    assert 60 <= heavy < medium <= 100
    assert rows["井场"][-1] == "一等地"


def test_table_4_9_without_zones_is_none():
    """评价单元只来自用户；没给分区就不出表（不造固定单元）。"""
    from app.services.reclamation_ch4_tables import table_4_9
    assert table_4_9({"area_m2": "23248"}) is None


def test_table_4_9_is_in_ch4_build():
    from app.services.reclamation_ch4_tables import build_ch4_tables
    caps = [t["caption"] for _, t in build_ch4_tables(
        {"area_m2": "23248", "land_use_zones": "井场 0.2310公顷；表土堆放区 0.3200公顷"})]
    assert "表4-9 土地适宜性分值" in caps
