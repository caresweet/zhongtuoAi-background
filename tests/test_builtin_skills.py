# -*- coding: utf-8 -*-
"""系统内置技能（五类模块）—— 目录、域过滤、注入与合并。"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import builtin_skills as bs


def test_catalog_ids_unique_and_modules_complete():
    ids = [s.id for s in bs.CATALOG]
    assert len(ids) == len(set(ids))
    assert {s.module for s in bs.CATALOG} == set(bs.MODULES) == {
        "retrieval", "tools", "calc", "integration", "audit"}


def test_rules_are_valid_and_domain_scoped():
    rules = bs.rules_for("stability")
    assert {r["skill_id"] for r in rules} >= {"b-audit-001", "b-audit-002"}
    for r in rules:
        assert r["source"] == "builtin"
        re.compile(r["pattern"])                 # 正则必须合法
        # replacement 为 None 表示「只提示、不自动改」（如 AI 热词、破折号堆叠）
        assert r["replacement"] is None or isinstance(r["replacement"], str)

    # 规则只对声明域生效（当前两条都是全域）
    assert bs.rules_for("bidding")


def test_texts_respect_domain():
    recl = {t["skill_id"] for t in bs.texts_for("reclamation")}
    stab = {t["skill_id"] for t in bs.texts_for("stability")}
    assert "b-calc-002" in recl and "b-calc-002" not in stab
    assert "b-calc-003" in stab and "b-calc-003" not in recl


def test_hints_grouped_by_module_and_domain_filtered():
    recl = bs.hints_for("reclamation")
    for title in ("### 检索", "### 工具", "### 计算", "### 外部对接", "### 审核校验"):
        assert title in recl
    assert "静态总投资不含价差预备费" in recl       # 复垦计算技能
    assert "支持率=100%" not in recl              # 稳评专属不进复垦

    bid = bs.hints_for("bidding")
    assert "### 检索" in bid and "### 审核校验" in bid
    assert "复垦工程量" not in bid
    assert "支持率=100%" not in bid


def test_catalog_filters_domain_and_module():
    items = bs.catalog(domain="reclamation", module="calc")
    assert items and all(i["module"] == "calc" for i in items)
    assert all("reclamation" in i["domains"] for i in items)


def test_get_active_skills_merges_builtin():
    from app.services.skill_service import get_active_skills
    skills = asyncio.run(get_active_skills("stability"))
    assert skills["builtin_total"] > 0
    assert any(r.get("source") == "builtin" for r in skills["rules"])
    assert any(t.get("source") == "builtin" for t in skills["texts"])


def test_rule_patterns_match_intended_text():
    rules = {r["skill_id"]: r for r in bs.rules_for("stability")}
    assert re.search(rules["b-audit-001"]["pattern"], "综上所述，项目可行。")
    assert not re.search(rules["b-audit-001"]["pattern"], "综合以上分析，项目可行。")
    assert re.search(rules["b-audit-002"]["pattern"], "联系人：XXX")


def test_builtin_rule_autofix_cleans_text():
    """内置规则命中后，审核 agent 能按 replacement 自动修正。"""
    from app.agent.agents.quality_review_agent import QualityReviewAgent

    rules = {r["skill_id"]: r for r in bs.rules_for("stability")}
    agent = QualityReviewAgent.__new__(QualityReviewAgent)
    state = {"chapters": {2: {"markdown": "综上所述，项目可行。联系人：XXX"}}}

    for skill_id in ("b-audit-002", "b-audit-001"):
        r = rules[skill_id]
        issue = {"chapter": 2, "type": "builtin_skill_violation",
                 "pattern": r["pattern"], "replacement": r["replacement"]}
        asyncio.run(agent._auto_fix_issue(state, issue))

    assert state["chapters"][2]["markdown"] == "项目可行。联系人：【待补充】"


def test_toggle_disables_skill(tmp_path, monkeypatch):
    """停用后不再注入 prompt、不再参与审核；重新启用恢复。"""
    from app.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    def has_rule():
        return any(r["skill_id"] == "b-audit-001" for r in bs.rules_for("stability"))

    assert has_rule()
    assert bs.set_enabled("b-audit-001", False)["updated"] == 1
    assert not has_rule()
    assert "综上所述" not in bs.hints_for("stability")
    assert bs.catalog(domain="stability")[0]["enabled"] in (True, False)

    assert bs.set_enabled("not-exist", False)["updated"] == 0
    assert bs.set_enabled("b-audit-001", True)["updated"] == 1
    assert has_rule()
