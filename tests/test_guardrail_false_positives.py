# -*- coding: utf-8 -*-
"""质检误报修复：反对率 0%、法规施行年、单位问卷、签名下划线。"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.validation.content_guardrails import (   # noqa: E402
    find_blocking_issues, find_data_validity_issues,
    find_hallucinated_regulations, validate_numeric_ranges,
)


def test_zero_opposition_not_flagged():
    text = "支持率为100.0%，反对率为0.0%，未出现群众反对。"
    issues = find_data_validity_issues(text)
    assert not [i for i in issues if "反对" in i.get("description", "")]


def test_positive_opposition_is_flagged():
    issues = find_data_validity_issues("反对率为5%，部分群众明确反对。")
    assert any("反对" in i.get("description", "") for i in issues)


def test_law_effective_year_not_flagged():
    text = "《中华人民共和国突发事件应对法》（2024年11月1日起施行）。"
    issues = find_data_validity_issues(text)
    assert not [i for i in issues if "年份" in i.get("description", "")]


def test_project_year_is_flagged():
    issues = find_data_validity_issues("本项目于2024年启动征收工作。")
    assert any("年份" in i.get("description", "") for i in issues)


def test_unit_questionnaire_small_count_not_flagged():
    text = ("向马头镇人民政府、码头社区及相关部门发放单位调查问卷3份，"
            "回收有效问卷3份。")
    assert validate_numeric_ranges(text) == []


def test_signature_underscores_not_placeholder():
    text = "评估助理：____________________\n资料员：____________________"
    assert find_blocking_issues(text) == []


def test_real_regulations_in_whitelist():
    text = ("《关于加强新形势下重大决策社会稳定风险评估机制建设的意见》"
            "（中办发〔2021〕11号）；《重大行政决策程序暂行条例》"
            "（中华人民共和国国务院令第713号）；《自然资源部关于规范临时用地管理的通知》"
            "（自然资规〔2021〕2号）。")
    assert find_hallucinated_regulations(text) == []


def test_standard_code_typo_repair():
    from app.services.reclamation_content_repairs import fix_standard_codes
    out, n = fix_standard_codes("《土地利用现状分类》，GB/T21010-20017；")
    assert "GB/T 21010-2017" in out and "20017" not in out
    assert n == 1
