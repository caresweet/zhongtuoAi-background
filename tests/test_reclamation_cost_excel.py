# -*- coding: utf-8 -*-
"""第七章投资估算 Excel（用户上传的管道费用表）→ 表6-1/7-3/7-4/7-5/8-1。"""

from pathlib import Path

import pytest

from app.services.reclamation_cost_excel import (
    engineering_table, other_cost_table, phase_schedule_table,
    quantities_from_workbook, read_cost_workbook, resolve_cost_excel,
    summary_table, to_budget_estimate,
)

XLSX = Path(__file__).resolve().parent.parent / "storage" / "templates" / \
    "投资估算_井神三井组.xlsx"

pytestmark = pytest.mark.skipif(not XLSX.exists(), reason="样本投资估算 Excel 不在仓库里")


@pytest.fixture(scope="module")
def wb():
    d = read_cost_workbook(XLSX)
    assert d, "投资估算 Excel 应能读出工程施工费"
    return d


def test_summary_numbers_match_sample(wb):
    sm = wb["summary"]
    assert round(sm["construction"], 2) == 85.01
    assert round(sm["other"], 2) == 13.74
    assert round(sm["monitor_care"], 2) == 19.38
    assert round(sm["reserve"], 2) == 17.31
    assert sm["static_total"] == 126.92
    assert sm["dynamic_total"] == 135.44
    assert sm["escalation"] == 8.52
    assert sm["monitor"] == 1.2752
    assert sm["care"] == 0.6           # 不能命中「监测与管护费」
    assert sm["pollution"] == 6 and sm["quality"] == 6
    assert sm["acceptance"] == 5.5
    assert round(sm["basic_reserve"], 2) == 7.09
    assert round(sm["risk"], 2) == 1.7


def test_engineering_table_matches_sample(wb):
    t = engineering_table(wb)
    assert t["headers"] == ["序号", "工程或费用名称", "单位", "数量",
                            "综合单价（元）", "工程施工费（万元）"]
    flat = [" ".join(r) for r in t["rows"]]
    assert any("表土剥离" in r and "1593.03" in r for r in flat)
    assert any("垫层清理" in r and "7425.64" in r for r in flat)
    assert any("沟渠疏通" in r and "45.00" in r for r in flat)
    assert t["rows"][-1][0] == "合计" and t["rows"][-1][-1] == "85.0127"


def test_other_cost_table_only_other_fees(wb):
    t = other_cost_table(wb)
    names = " ".join(" ".join(r) for r in t["rows"])
    assert "前期工作费" in names and "业主管理费" in names
    assert "监测与管护费" not in names and "预备费" not in names
    assert t["headers"][-1] == "13.74"
    assert t["rows"][0][0] == "（一）" and t["rows"][0][1] == "前期工作费"


def test_summary_table_has_rates(wb):
    t = summary_table(wb)
    rows = {r[1]: r for r in t["rows"]}
    assert rows["基本预备费"][3] == "6%[一+二+三+四]"
    assert rows["风险费"][3] == "2%[一]"
    assert rows["复垦监测费"][3] == "1.5%[一]"
    assert rows["动态总投资"][2] == "135.44"


def test_quantities_from_workbook_feed_table_6_1(wb):
    rows = quantities_from_workbook(wb)
    assert len(rows) == 12
    first = rows[0]
    assert first["name"] == "表土剥离" and first["qty"] == 66.645
    assert first["section"] == "土壤剥离工程"


def test_phase_schedule_sums_to_dynamic_total(wb):
    filled = {"land_use_start": "2026-10", "land_use_end": "2028-09",
              "reclaim_period_months": "6", "care_period_months": "6"}
    t = phase_schedule_table(wb, filled)
    assert t is not None
    assert len(t["rows"]) == 4
    labels = [r[0] for r in t["rows"]]
    assert labels[0] == "2026年10月"
    assert labels[-1] == "2029年4月至2029年9月"
    total = round(sum(float(r[1]) for r in t["rows"]), 2)
    assert total == round(wb["summary"]["dynamic_total"], 2)


def test_phase_schedule_needs_dates(wb):
    assert phase_schedule_table(wb, {}) is None


def test_budget_estimate_matches_excel(wb):
    est = to_budget_estimate(wb, area_m2=23248)
    assert round(est.construction, 2) == 85.01
    assert round(est.dynamic_total, 2) == 135.44
    assert round(est.acceptance, 2) == 17.5      # 6 + 6 + 5.5
    assert est.per_ha > 0


def test_resolve_cost_excel_by_name(tmp_path):
    p = tmp_path / "费用表.xlsx"
    p.write_bytes(b"x")
    got = resolve_cost_excel({"cost_estimate_excel_name": "费用表.xlsx"},
                             [], tmp_path)
    assert got == str(p)
