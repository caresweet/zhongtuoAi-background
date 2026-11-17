# -*- coding: utf-8 -*-
"""基础资料弹窗：字段字典与写回测试。"""

from app.services.intake_form import apply_form, form_schema


def test_stability_schema_has_groups_and_required():
    s = form_schema("stability")
    titles = [g["title"] for g in s["groups"]]
    assert titles == ["项目基本信息", "时间节点", "调查数据", "人员与联系"]
    keys = {f["key"] for g in s["groups"] for f in g["fields"]}
    assert {"project_name", "area_mu", "support_rate", "leader"} <= keys
    assert any(f["required"] for g in s["groups"] for f in g["fields"])


def test_reclamation_schema_has_tables():
    s = form_schema("reclamation")
    assert [t["key"] for t in s["tables"]] == ["zones", "land_rows"]
    keys = {f["key"] for g in s["groups"] for f in g["fields"]}
    assert {"temp_area_ha", "excavation_area_ha", "reclaim_direction"} <= keys
    # 预算字段用映射后的 key，不是中文标签
    assert "static_total" in keys and "动态总投资(万元)" not in keys


def test_apply_stability_form_writes_fields():
    filled = {}
    changed, missing = apply_form("stability", filled, {"values": {
        "project_name": "测试征收项目", "area_mu": "12.5",
        "support_rate": "100", "leader": "张三",
    }})
    assert filled["project_name"] == "测试征收项目"
    assert filled["area_mu"] == "12.5"
    assert changed and "project_name" in changed
    assert filled["_intake_source"] == "form"
    assert missing  # 其余必填未填


def test_apply_reclamation_form_derives_area_zones_land_budget():
    filled = {}
    payload = {
        "values": {
            "project_name": "复垦测试", "build_unit": "某公司",
            "construction_nature": "新建", "location": "淮安市淮安区某镇某村",
            "region": "淮安区", "permanent_area_ha": "1.2",
            "land_use_period_months": "24", "land_use_start": "2025-01",
            "land_use_end": "2027-01", "reclaim_period_months": "6",
            "care_period_months": "6", "land_location": "某村",
            "land_ownership": "村集体", "temp_area_ha": "2.5",
            "topsoil_thickness": "30", "hard_surface_type": "碎石垫层+钢板",
            "hard_area_ha": "0.8", "occupies_basic_farmland": "否",
            "occupies_eco_redline": "否", "reclaim_direction": "旱地",
        },
        "zones": [{"name": "井场", "area_ha": "1.0", "method": "压占",
                   "degree": "中度", "land_types": "耕地",
                   "reclaim_direction": "旱地"}],
        "land_rows": [{"level1": "耕地", "level2": "旱地",
                       "area_ha": "2.0", "ownership": "某村"}],
        "budget": {"static_total": "50", "dynamic_total": "52"},
    }
    changed, missing = apply_form("reclamation", filled, payload)
    assert filled["area_m2"] == 25000.0
    assert filled["permanent_area_m2"] == 12000.0
    assert filled["project_area_m2"] == 37000.0
    assert filled["hard_area_m2"] == 8000.0
    assert filled["service_years"] == 3.0
    assert len(filled["_land_use_zones"]) == 1
    assert filled["land_classification"]["合计_m2"] == 20000.0
    assert filled["_budget_estimate"]["static_total"] == "50"
    assert missing == []
    assert changed
