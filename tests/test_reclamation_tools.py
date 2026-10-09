"""复垦专属工具 —— 工程量测算 / 投资估算 / 适宜性评价。

要点：
  · 复垦工具表＝复垦自己的几件；稳评专有器物不进表；稳评工具表逐字不变。
  · 每个计算工具返回的每个数都是 `TracedValue`（值 + 来源 + 算法），并附 `audit()`。
  · 数值对得上样本报告（井神）：表土剥离 580.8m³、其他费用≈1.10万、静态总投资≈19.84万。
"""
import asyncio
import json

from app.agent.tools import TOOL_REGISTRY, get_tools_schema, execute_tool_calls


# ═══════════════════════════════════════════════════════════════
# 1. 注册表 / 按域过滤
# ═══════════════════════════════════════════════════════════════

def test_reclamation_tools_registered():
    assert {"calc_earthwork", "calc_investment", "evaluate_suitability"} <= set(TOOL_REGISTRY)


def test_stability_tool_table_unchanged():
    """稳评工具表＝原五件，顺序与名称均不变。"""
    names = [f["function"]["name"] for f in get_tools_schema("stability")]
    assert names == ["retrieve_rag", "parse_kd_table", "calc_risk_scoring",
                     "query_survey", "query_dept_survey"]


def test_reclamation_tool_table_excludes_stability_instruments():
    names = [f["function"]["name"] for f in get_tools_schema("reclamation")]
    assert names == ["retrieve_rag", "parse_kd_table",
                     "calc_earthwork", "calc_investment", "evaluate_suitability"]
    assert "calc_risk_scoring" not in names
    assert "query_survey" not in names and "query_dept_survey" not in names


def test_retrieve_rag_description_is_domain_scoped():
    """`retrieve_rag` 描述按域取：稳评点名 DB32/T4013，复垦点名复垦条例；互不串。"""
    stab = {f["function"]["name"]: f["function"]["description"]
            for f in get_tools_schema("stability")}["retrieve_rag"]
    rec = {f["function"]["name"]: f["function"]["description"]
           for f in get_tools_schema("reclamation")}["retrieve_rag"]
    assert "DB32/T4013-2021" in stab and "复垦" not in stab
    assert "复垦" in rec and "DB32" not in rec


def test_parse_kd_table_passes_reclamation_profile(monkeypatch):
    """复垦域调用时，勘测定界解析走 `RECLAMATION_PROFILE`；稳评走默认 profile。"""
    import app.services.land_survey_parser as lsp
    seen = {}

    def _fake(path, profile=None):
        seen["profile"] = profile
        return {}

    monkeypatch.setattr(lsp, "parse_land_survey", _fake)

    asyncio.run(execute_tool_calls(
        [{"name": "parse_kd_table", "id": "1", "arguments": {"pdf_path": "/tmp/x.pdf"}}],
        {"domain": "reclamation"}))
    assert seen["profile"] is lsp.RECLAMATION_PROFILE

    asyncio.run(execute_tool_calls(
        [{"name": "parse_kd_table", "id": "2", "arguments": {"pdf_path": "/tmp/x.pdf"}}],
        {"domain": "stability"}))
    assert seen["profile"] is lsp.DEFAULT_PROFILE


# ═══════════════════════════════════════════════════════════════
# 2. 工程量测算（对样本：井神 6.1.1/6.2）
# ═══════════════════════════════════════════════════════════════

def _run(name, args, ctx=None):
    res = asyncio.run(execute_tool_calls([{"name": name, "id": "t", "arguments": args}], ctx or {}))
    return json.loads(res[0]["content"])


def _val(payload, key):
    return {e["名称"]: e for e in payload["数值"]}[key]


def test_calc_earthwork_matches_sample():
    p = _run("calc_earthwork", {
        "soil_area_m2": 1936, "functional_area_m2": 2354, "ditch_area_m2": 393,
    })
    assert _val(p, "表土剥离量")["值"] == 580.8          # 1936 × 0.3
    assert _val(p, "表土剥离量_百m3")["值"] == 5.808      # ÷100
    assert _val(p, "混凝土层工程量")["值"] == 235.4       # 2354 × 0.10
    assert _val(p, "垫层工程量")["值"] == 188.32          # 2354 × 0.08
    assert _val(p, "沟渠疏通量")["值"] == 589.5           # 393 × 1.5
    assert p.get("审计") is None                          # 账本干净


def test_calc_earthwork_values_are_traced():
    """每个工程量都带算法（不是裸 float）。"""
    p = _run("calc_earthwork", {"soil_area_m2": 1936})
    v = _val(p, "表土剥离量")
    assert v["算法"] == "耕地面积 * 剥离厚度"
    assert v["单位"] == "m3"


def test_calc_earthwork_no_area_returns_hint():
    p = _run("calc_earthwork", {})
    assert p["数值"] == []
    assert "未提供任何面积" in p["说明"]


# ═══════════════════════════════════════════════════════════════
# 3. 投资估算（对样本：井神 第7章 与 表7-4/7-5）
# ═══════════════════════════════════════════════════════════════

def test_calc_investment_matches_sample():
    """工程施工费 6.79 万、复垦验收费 10 万 → 其他费用≈1.10、监测管护≈10.70、静态≈19.84。"""
    p = _run("calc_investment", {
        "engineering_cost": 6.79, "acceptance_fee": 10.0,
        "monitoring_persons": 1, "monitoring_months": 6,
    })
    assert abs(_val(p, "其他费用")["值"] - 1.10) < 0.02
    assert abs(_val(p, "监测与管护费")["值"] - 10.70) < 0.02
    assert abs(p["静态总投资"] - 19.84) < 0.02
    assert _val(p, "其他费用")["算法"]        # 有算法（可反查）
    assert p.get("审计") is None


def test_calc_investment_other_fee_item_formula_documented():
    """其他费用各项的算法＝样本单元格里的式子（如 土地清查费 = 工程施工费×0.20%）。"""
    p = _run("calc_investment", {"engineering_cost": 6.79})
    assert _val(p, "土地清查费")["算法"] == "(工程施工费) * 0.002"
    assert _val(p, "业主管理费")["值"] > 0    # 基数含前面几组小计，不报 KeyError


def test_calc_investment_synthesizes_from_direct_costs():
    """给出各工程类别直接工程费 → 按 表7-1/7-2 合成工程施工费。"""
    p = _run("calc_investment", {"direct_costs": {"土方工程": 5.0, "混凝土工程": 1.0}})
    assert _val(p, "工程施工费")["值"] > 6.0           # 直接费+间接费+利润+税金 ≥ 直接工程费
    assert _val(p, "措施费_土方工程")["值"] == 5.0 * 0.034
    assert _val(p, "间接费_混凝土工程")["值"] == 1.0 * 0.06


def test_calc_investment_without_input_returns_hint():
    p = _run("calc_investment", {})
    assert p["数值"] == []
    assert "engineering_cost" in p["说明"]


# ═══════════════════════════════════════════════════════════════
# 4. 适宜性评价（对样本：井神 表9/表10）
# ═══════════════════════════════════════════════════════════════

def test_evaluate_suitability_weighted_total_and_grade():
    scores = {"自然坡度": 100, "有效土层厚度": 90, "土壤质地": 80, "有机质含量": 90,
              "地貌部位": 100, "灌溉保证程度": 90, "灌溉方式": 90, "排水保证程度": 90,
              "道路通达性": 70, "田块平整": 80}
    p = _run("evaluate_suitability", {"unit_name": "施工便道", "factor_scores": scores})
    assert abs(p["加权总分"] - 88.3) < 1e-6
    assert p["等别"] == "一等地"
    assert _val(p, "加权总分")["算法"]
    assert p.get("审计") is None


def test_evaluate_suitability_grade_thresholds():
    low = {k: 50 for k in
           ["自然坡度", "有效土层厚度", "土壤质地", "有机质含量", "地貌部位",
            "灌溉保证程度", "灌溉方式", "排水保证程度", "道路通达性", "田块平整"]}
    assert _run("evaluate_suitability", {"factor_scores": low})["等别"] == "三等地"


def test_evaluate_suitability_weights_override():
    p = _run("evaluate_suitability",
             {"factor_scores": {"自然坡度": 100, "土壤质地": 60}, "weights": {"自然坡度": 1.0}})
    assert p["加权总分"] == 100.0 and p["等别"] == "一等地"


def test_evaluate_suitability_missing_factor_reported():
    p = _run("evaluate_suitability", {"factor_scores": {"自然坡度": 100}})
    assert "缺因子分值" in p["说明"]
