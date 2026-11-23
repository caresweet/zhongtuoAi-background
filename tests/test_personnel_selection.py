# -*- coding: utf-8 -*-
"""报告人员：未勾选时随机取 5 名项目组成员，证书优先给有扫描件的人。"""

from __future__ import annotations

from app.services.report_assembler import ReportAssembler


def _people(n=10):
    return [{"姓名": f"P{i}", "拟任角色": "项目组成员"} for i in range(n)]


def test_sample_team_picks_five_members():
    team = ReportAssembler._sample_team(_people(10), 5)
    assert len(team) == 5
    assert len({p["姓名"] for p in team}) == 5


def test_sample_team_prefers_certified_members():
    people = _people(10)
    certified = {f"P{i}" for i in range(5)}
    team = ReportAssembler._sample_team(people, 5, certified)
    assert {p["姓名"] for p in team} == certified


def test_sample_team_returns_all_when_small():
    people = [{"姓名": "A"}, {"姓名": "B"}]
    assert ReportAssembler._sample_team(people, 5) == people
