# -*- coding: utf-8 -*-
"""本次修复的针对性测试：问卷数据、标题规范化、location 清洗、第6/8章兜底。

运行：cd backend && python -m pytest tests/test_survey_fixes.py -v
"""
import sys
from collections import Counter

sys.path.insert(0, "..") if __name__ == "__main__" else None

from app.services.survey_data import (
    merge_verified_survey_into_state,
    get_verified_survey_analysis,
    match_dataset_key,
)
from app.services.table_generation_service import TableGenerationService


# ═══════════════════════════════════════════════════════════════════
# 1. 问卷数据：核验数据集合并
# ═══════════════════════════════════════════════════════════════════

def test_verified_survey_merge_fills_core_fields():
    state = {
        "filled_data": {"doc_reference": "洪拟征告〔2026〕7号", "household_count": "26"},
        "generated_sections": {},
    }
    ok = merge_verified_survey_into_state(state)
    assert ok is True
    filled = state["filled_data"]
    # 问卷数=50（修正 26户→50份）
    assert filled["total_samples"] == "50"
    assert filled["support_count"] == "50"
    assert filled["support_rate"] == "100%"
    sa = state["generated_sections"]["survey_analysis"]
    assert sa["total_samples"] == 50
    assert sa["support_rate"] == 100.0
    # 六维完整
    assert sum(sa["identity_groups"].values()) == 50
    assert sum(sa["age_groups"].values()) == 50
    assert sum(sa["occupation_groups"].values()) == 50
    assert sa["awareness_levels"] == {"了解": 50}
    assert sa["attitude_options"] == {"支持": 50}
    assert sum(sa["resolution_methods"].values()) == 50


def test_verified_survey_no_match_returns_false():
    state = {"filled_data": {"doc_reference": "某某项目"}, "generated_sections": {}}
    assert merge_verified_survey_into_state(state) is False


def test_dataset_key_match_handles_bracket_variants():
    assert match_dataset_key("洪拟征告〔2026〕7号") == "洪拟征告〔2026〕7号"
    assert match_dataset_key("洪拟征告（2026）7号 土地征收预公告") == "洪拟征告〔2026〕7号"


def test_table_3_1_six_dimensions():
    state = {
        "filled_data": {"doc_reference": "洪拟征告〔2026〕7号"},
        "generated_sections": {},
    }
    merge_verified_survey_into_state(state)
    t31 = TableGenerationService.generate_table_3_1(state)
    assert t31["headers"] == ["调查内容", "选项", "个数", "比例"]
    rows = t31["rows"]
    # 六维都在（题面照抄《稳评问卷调查表》第一列原文）
    dim_questions = ["您是", "您的年龄", "您的职业",
                     "您是否了解本决策", "对该决策是否支持",
                     "您会采取何种方式解决诉求"]
    q_in_rows = {r[0] for r in rows}
    for q in dim_questions:
        assert q in q_in_rows, f"缺少维度问题行: {q}"
    # 关键数值：支持50、了解50、调解39
    cell_map = {(r[0], r[1]): r[2] for r in rows}
    assert cell_map[("对该决策是否支持", "支持")] == "50"
    assert cell_map[("您是否了解本决策", "了解")] == "50"
    assert cell_map[("您的年龄", "36～55岁")] == "26"
    assert cell_map[("您会采取何种方式解决诉求", "调解")] == "39"


def test_table_3_3_dept_survey():
    state = {
        "filled_data": {"doc_reference": "洪拟征告〔2026〕7号"},
        "generated_sections": {},
    }
    merge_verified_survey_into_state(state)
    t33 = TableGenerationService.generate_table_3_3(state)
    assert t33["headers"] == ["调查内容", "选项", "个数", "比例"]
    rows = t33["rows"]
    cell_map = {(r[0], r[1]): r[2] for r in rows}
    assert cell_map[("贵单位对本决策的了解程度如何？", "了解")] == "2"
    assert cell_map[("贵单位对本决策实施的基本态度是？", "支持")] == "2"


# ═══════════════════════════════════════════════════════════════════
# 2. 标题规范化
# ═══════════════════════════════════════════════════════════════════

from app.agent.agents.chapters.chapter_base import ChapterAgentBase


class _Dummy(ChapterAgentBase):
    chapter_number = 4
    chapter_title = "决策综合分析"


def test_heading_normalizer_converts_invented_headings():
    d = _Dummy()
    md = (
        "### （一）项目基本情况\n正文\n"
        "### 1.1 决策主体与权限\n内容1\n"
        "### 4.1.2 征收目的与规划用途的关系\n内容2\n"        "### （二）四性分析\n正文\n"
        "### （二）权属地类构成\n内容3\n"
        "## 六、措施前风险等级研判\n"
        "### （三）不稳定风险因素\n内容4\n"
    )
    out = d._normalize_headings(md)
    # 标准二级标题保留
    # 🔴 体例已统一为（一）（二）（三）（照参照件 1号），4.1/1.1 这类不再是标准标题。
    assert "### （一）项目基本情况" in out
    assert "### （二）四性分析" in out
    assert "### （三）不稳定风险因素" in out
    # 自创标题 → （一）（二）
    assert "**（一）决策主体与权限**" in out  # 自创标题 → 中文小点
    assert "**（二）征收目的与规划用途的关系**" in out
    assert "**（一）权属地类构成**" in out
    # 中文序号标题删除
    assert "六、措施前风险等级研判" not in out


# ═══════════════════════════════════════════════════════════════════
# 3. location JSON 清洗
# ═══════════════════════════════════════════════════════════════════

def test_location_sanitizer_strips_vision_dict():
    d = _Dummy()
    dirty = "{'general_area': '疑似城乡结合部或农村地区', 'visual_landmarks': '左侧有招牌'}"
    assert d._sanitize_filled_value(dirty) == ""
    assert d._sanitize_filled_value("淮安市洪泽区朱坝街道三圩社区") == "淮安市洪泽区朱坝街道三圩社区"
    assert d._sanitize_filled_value(None) == ""
    assert d._sanitize_filled_value("") == ""


# ═══════════════════════════════════════════════════════════════════
# 4. 第6/8章兜底内容（按 12 章结构规范输出骨架，不写死、不编造）
# ═══════════════════════════════════════════════════════════════════

from app.agent.agents.chapters.chapter_6_agent import Chapter6Agent
from app.agent.agents.chapters.chapter_11_agent import Chapter11Agent


def test_ch11_fallback_uses_section_specs():
    """第11章=分析预测风险源；小节名按项目动态生成，兜底不应留空。"""
    state = {"filled_data": {"doc_reference": "洪拟征告〔2026〕7号", "support_rate": "100"}}
    fb = Chapter11Agent()._fallback_content(state)
    assert "第11章" in fb or "分析预测风险源" in fb
    assert "八、" not in fb

def test_ch11_fallback_uses_section_specs():
    """第11章=分析预测风险源；小节名按项目动态生成，兜底不应留空。"""
    state = {"filled_data": {"doc_reference": "洪拟征告〔2026〕7号", "support_rate": "100"}}
    fb = Chapter11Agent()._fallback_content(state)
    assert "第11章" in fb or "分析预测风险源" in fb
    assert "八、" not in fb

# ═══════════════════════════════════════════════════════════════════
# 5. 第3章表3-1 数据注入
# ═══════════════════════════════════════════════════════════════════

def test_ch3_land_table_data_builder():
    from app.agent.agents.chapters.chapter_3_agent import Chapter3Agent
    tbl = {
        "headers": ["被征用地单位", "权属类别", "面积总计", "耕地", "林地"],
        "raw_markdown": "| 朱坝街道农民集体 | 镇集体 | 8284 | ... |",
        "row_count": 6,
    }
    text = Chapter3Agent._build_land_table_data({"_extracted_tables": [tbl]})
    assert "8284" in text
    # 无提取表 → 空串
    assert Chapter3Agent._build_land_table_data({}) == ""
