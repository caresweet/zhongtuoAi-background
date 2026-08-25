"""项目事实库 + 事实比对器 单元测试（治「数据不准」B+C）。

验证：报告各章关键数字回对资料，错数字检出、对数字不误报。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.validation.content_guardrails import (
    build_project_facts,
    check_chapters_against_facts,
    _norm_num,
)


def test_norm_num():
    """数字归一：去 %、全角、逗号、中文单位。"""
    assert _norm_num('100.0%') == 100.0
    assert _norm_num('100') == 100.0
    assert _norm_num('489.51亩') == 489.51
    assert _norm_num('1,000') == 1000.0
    assert _norm_num('100％') == 100.0          # 全角 %
    assert _norm_num('') is None
    assert _norm_num('【待补充】') is None


def test_build_project_facts():
    """事实库只收口归一，不猜测；缺字段留空。"""
    facts = build_project_facts({
        'area_mu': '489.51', 'area_m2': '326342', 'support_rate': '100%',
        'total_samples': '52', 'household_count': '49',
        'doc_reference': '洪拟征告〔2026〕7号',
    })
    assert facts['area_mu'] == 489.51
    assert facts['area_m2'] == 326342.0
    assert facts['support_rate'] == 100.0
    assert facts['total_samples'] == 52.0
    assert facts['household_count'] == 49.0
    assert facts['doc_reference'] == '洪拟征告〔2026〕7号'
    # 缺数据 → 不放进事实库
    empty = build_project_facts({})
    assert 'area_mu' not in empty


def test_area_mismatch_detected():
    """报告写 500亩，资料 489.51亩 → 检出。"""
    facts = build_project_facts({'area_mu': '489.51', 'area_m2': '326342'})
    issues = check_chapters_against_facts({1: {'markdown': '本次拟征收总面积500亩。'}}, facts)
    assert any(i['type'] == 'area_mismatch' for i in issues)


def test_area_correct_not_flagged():
    """报告写 489.51亩 / 326342㎡，与资料一致 → 不误报。"""
    facts = build_project_facts({'area_mu': '489.51', 'area_m2': '326342'})
    issues = check_chapters_against_facts(
        {6: {'markdown': '本次征收面积489.51亩（326342平方米）。'}}, facts)
    assert not any(i['type'] == 'area_mismatch' for i in issues)


def test_support_rate_mismatch_detected():
    """报告支持率 89.5%，资料 100% → 检出。"""
    facts = build_project_facts({'support_rate': '100%'})
    issues = check_chapters_against_facts({3: {'markdown': '群众支持率89.5%。'}}, facts)
    assert any(i['type'] == 'support_rate_mismatch' for i in issues)


def test_support_rate_correct_not_flagged():
    """支持率 100% 与资料一致 → 不误报。"""
    facts = build_project_facts({'support_rate': '100%'})
    issues = check_chapters_against_facts({3: {'markdown': '群众支持率100%。'}}, facts)
    assert not any(i['type'] == 'support_rate_mismatch' for i in issues)


def test_survey_count_mismatch_detected():
    """报告问卷 50份，资料 52份 → 检出。"""
    facts = build_project_facts({'total_samples': '52'})
    issues = check_chapters_against_facts({3: {'markdown': '发放问卷50份。'}}, facts)
    assert any(i['type'] == 'survey_count_mismatch' for i in issues)


def test_dedup_same_issue():
    """同一章同一类型只报一条（去重）。"""
    facts = build_project_facts({'area_mu': '489.51'})
    issues = check_chapters_against_facts(
        {1: {'markdown': '面积500亩，另有300亩。'}}, facts)
    area_issues = [i for i in issues if i['type'] == 'area_mismatch']
    assert len(area_issues) == 1
