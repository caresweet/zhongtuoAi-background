"""Agent tool registry tests — executor dispatch, exception safety, schemas."""

import asyncio
import pytest

from app.agent.tools import TOOL_REGISTRY, ALL_TOOLS_SCHEMA, execute_tool_calls


def test_three_tools_registered():
    assert {"retrieve_rag", "parse_kd_table", "calc_risk_scoring",
            "query_survey", "query_dept_survey"} <= set(TOOL_REGISTRY.keys())
    assert len(TOOL_REGISTRY) >= 3


def test_schemas_are_openai_format():
    for schema in ALL_TOOLS_SCHEMA:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] in TOOL_REGISTRY
        assert "description" in fn
        assert fn["parameters"]["type"] == "object"


def test_unknown_tool_safe():
    res = asyncio.run(execute_tool_calls([{"name": "nope", "id": "1", "arguments": {}}], {}))
    assert len(res) == 1
    assert res[0]["role"] == "tool"
    assert "未注册" in res[0]["content"]


def test_empty_tool_calls():
    assert asyncio.run(execute_tool_calls([], {})) == []


def test_calc_risk_scoring_tool():
    """评分工具返回扣分制 JSON，pre_total 合理、含 risk_level。"""
    res = asyncio.run(execute_tool_calls(
        [{"name": "calc_risk_scoring", "id": "t1", "arguments": {"support_rate": 95}}],
        {},
    ))
    import json
    data = json.loads(res[0]["content"])
    assert "pre_total" in data
    assert data["pre_total"] <= 13
    assert data["risk_level"] in ("低风险", "中风险", "高风险")


def test_parse_kd_table_no_pdf_returns_hint():
    """无勘测定界 pdf 时返回提示而非抛错。"""
    res = asyncio.run(execute_tool_calls(
        [{"name": "parse_kd_table", "id": "t2", "arguments": {}}],
        {"file_paths": ["/tmp/某公告.pdf"]},
    ))
    assert res[0]["content"]
    assert "未找到" in res[0]["content"] or "解析" in res[0]["content"]


def test_query_survey_with_data():
    """query_survey 返回六维统计文本。"""
    sa = {
        "total_samples": 54, "support_count": 54, "support_rate": 100.0,
        "oppose_count": 0, "oppose_rate": 0.0,
        "identity_groups": {"本地居民": 50, "附近上班": 4},
        "age_groups": {"36~55": 30, "56以上": 24},
    }
    res = asyncio.run(execute_tool_calls(
        [{"name": "query_survey", "id": "s1", "arguments": {}}],
        {"survey_analysis": sa},
    ))
    content = res[0]["content"]
    assert "总样本：54" in content
    assert "本地居民:50" in content
    assert "身份" in content


def test_query_survey_no_data_returns_hint():
    res = asyncio.run(execute_tool_calls(
        [{"name": "query_survey", "id": "s2", "arguments": {}}],
        {"survey_analysis": {}},
    ))
    assert "无公众问卷" in res[0]["content"]