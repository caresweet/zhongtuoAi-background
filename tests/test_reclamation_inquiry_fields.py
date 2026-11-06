# -*- coding: utf-8 -*-
"""复垦「数据核对」弹窗只能是复垦字段，不能问回稳评那 15 项。

🔴 线上事故（2026-11-02）：生成复垦报告时，开头「数据核对」弹窗又列出了
   `org_name`（稳评责任单位）、`support_rate`（群众支持率）、`household_count`（户数）、
   `area_mu`（亩）、`notice_date`（现场公示）…… 用户看到的是**稳评的清单**。

根因：`node_data_inquiry` 的 ② 段只按 `data_usage_map.FIELD_USAGE` 取候选，
   那是土地征收稳评的口径，没有域分支。复垦已有自己的
   `RECLAMATION_NEED_FIELDS`（项目名称/用地单位/单位地址/面积/文号…），必须按域取。
"""

from __future__ import annotations

import asyncio

import pytest

_STABILITY_ONLY = {
    "org_name", "support_rate", "household_count", "area_mu", "notice_date",
    "implement_unit", "compensation_standard", "total_samples",
    "survey_date", "symposium_date", "review_date", "notice_publish_date",
}


def _interrupt_fields(state: dict) -> list:
    """跑最小图，取 `data_inquiry` 中断时给前端的 `missing_fields`。"""
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver

    from app.services.report_workflow import node_data_inquiry

    g = StateGraph(dict)
    g.add_node("inquiry", node_data_inquiry)
    g.set_entry_point("inquiry")
    g.add_edge("inquiry", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": f"t_{abs(hash(str(state)))}"}}

    async def _go():
        async for ev in app.astream(dict(state), cfg):
            if "__interrupt__" in ev:
                iv = ev["__interrupt__"][0]
                return (getattr(iv, "value", iv) or {}).get("missing_fields") or []
        return []

    return asyncio.run(_go())


def test_reclamation_inquiry_does_not_ask_stability_fields():
    fields = _interrupt_fields({"_domain": "reclamation", "filled_data": {}})
    assert fields, "复垦缺全部字段时应当弹窗"
    keys = {f["key"] for f in fields}
    assert not (keys & _STABILITY_ONLY), f"复垦弹窗里混进了稳评字段: {keys & _STABILITY_ONLY}"
    assert keys <= {
        "project_name", "project_unit", "unit_address", "location",
        "area_m2", "doc_reference", "legal_rep", "contact_phone", "unit_nature",
        # 报告表「方案编制单位」栏（公司档案里没有，弹窗补问）
        "qualification_authority", "qualification_level",
        "company_contact", "company_phone",
        # 报告表「项目区面积 / 投资规模 / 项目性质 / 服务年限」—— 人工稿口径
        "project_area_m2", "project_investment", "construction_nature",
        "service_years", "topsoil_thickness",
        "reclaim_period_months", "care_period_months", "land_use_zones",
        # 设计参数（不设范文默认值，每次新报告都问）
        "hard_surface_type", "hard_area_m2", "surface_thickness_cm",
        "cushion_thickness_cm",
    }


def test_reclamation_inquiry_skips_values_already_recognized():
    fields = _interrupt_fields({
        "_domain": "reclamation",
        "filled_data": {"project_name": "井神临时用地", "area_m2": "23248"},
    })
    keys = {f["key"] for f in fields}
    assert "project_name" not in keys and "area_m2" not in keys
    assert "unit_address" in keys


def test_reclamation_inquiry_respects_confirmed_skip():
    fields = _interrupt_fields({
        "_domain": "reclamation",
        "filled_data": {"_confirmed_project_name": True},
    })
    assert "project_name" not in {f["key"] for f in fields}


def test_stability_inquiry_unchanged():
    """稳评那条路一行不动：仍会问到稳评专有字段。"""
    fields = _interrupt_fields({"_domain": "stability", "filled_data": {}})
    assert fields, "稳评缺字段时应当弹窗"
    assert "org_name" in {f["key"] for f in fields}


def test_global_filter_drops_stability_fields_for_reclamation():
    """最后一道闸：任何询问点漏进稳评字段，出口统一剔除。"""
    from app.agent.chapter_catalog import filter_inquiry_fields

    fields = [{"key": "org_name"}, {"key": "support_rate"},
              {"key": "project_name"}, {"key": "_gap_1_0"}]
    keys = [f["key"] for f in filter_inquiry_fields("reclamation", fields)]
    assert keys == ["project_name", "_gap_1_0"]


def test_global_filter_keeps_stability_untouched():
    from app.agent.chapter_catalog import filter_inquiry_fields

    fields = [{"key": "org_name"}, {"key": "support_rate"}]
    assert filter_inquiry_fields("stability", fields) == fields
    assert filter_inquiry_fields(None, fields) == fields


def test_global_filter_specs_for_reclamation():
    from app.agent.chapter_catalog import filter_inquiry_specs

    specs = [("org_name", "稳评责任单位", "text", None, 100, ""),
             ("project_name", "项目名称", "text", None, 60, "")]
    assert [s[0] for s in filter_inquiry_specs("reclamation", specs)] == ["project_name"]
