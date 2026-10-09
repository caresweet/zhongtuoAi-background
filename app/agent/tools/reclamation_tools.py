# -*- coding: utf-8 -*-
"""复垦专属工具 —— 工程量测算 / 投资估算 / 适宜性评价。

为什么单开一件模块
────────────────────────────────────────────────────────────────
注册表 `registry.py` 里的五件工具**全为稳评器物**（DB32 扣分制 / 稳评公众·部门问卷 /
稳评勘测定界表型 / 点名「稳评规范」的 RAG 描述）。复垦章的正文大半是**算出来的**
（第6章工程量、第7章投资、第4章适宜性），却一直没有自己的工具，只能整轮跳过工具回合。
这里补上复垦的三件计算工具，**只对复垦开放**（`domains=("reclamation",)`）——
稳评拿到的工具表因此一字不动。

数字一律走数据层
────────────────────────────────────────────────────────────────
每个结果都是 `ReclamationDataModel` 里的一个 `TracedValue`：值 + 来源 + 算法。
工具返回时**连同 `audit()` 一起给出**，谁都能反查一个数是怎么来的、并当场发现
「算法与文中值不自洽」这类缺陷（样本里真实出现过）。详见
`app/services/reclamation_data_model.py`。

规则从哪来（不臆造）
────────────────────────────────────────────────────────────────
`措施费/间接费取费费率表`（表7-1/7-2）与 `其他费用` 各项费率、监测费、预备费、
风险金费率，均**逐字取自业主 5 份真实方案**并交叉验证一致，见各常量注释；
调用方可经参数覆盖。适宜性评价的**因子权重**取自井神/污水处理厂两份样本的
三级因子表（表9），**分级标准**（因素特征→分值）因样本抽取有噪声，本工具**不实现**，
只接收评价单元各因子的**分值**（表10 口径）。
"""

import json
import logging
from typing import Any, Dict, List

from .registry import register_tool

logger = logging.getLogger(__name__)

_RECLAMATION = ("reclamation",)


# ═══════════════════════════════════════════════════════════════
# 规则表（跨项目不变；来源：业主 5 份真实方案）
# ═══════════════════════════════════════════════════════════════

# 表7-1 措施费取费费率表 —— 井神 / 王元佳苑 / 明远 / 污水处理厂 / 宁淮城际 **5 份逐字一致**。
# {工程类别: {临时设施费, 冬雨季施工增加费, 施工辅助费, 安全施工措施费}}（单位：%）
MEASURE_FEE_RATES: Dict[str, Dict[str, float]] = {
    "土方工程":   {"临时设施费": 2, "冬雨季施工增加费": 0.5, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "石方工程":   {"临时设施费": 2, "冬雨季施工增加费": 0.5, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "砌体工程":   {"临时设施费": 2, "冬雨季施工增加费": 1.0, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "混凝土工程": {"临时设施费": 3, "冬雨季施工增加费": 1.0, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "农用井工程": {"临时设施费": 3, "冬雨季施工增加费": 1.0, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "其他工程":   {"临时设施费": 2, "冬雨季施工增加费": 1.0, "施工辅助费": 0.7, "安全施工措施费": 0.2},
    "安装工程":   {"临时设施费": 3, "冬雨季施工增加费": 1.0, "施工辅助费": 1.0, "安全施工措施费": 0.3},
}

# 表7-2 间接费取费费率表 —— 同上 5 份一致。安装工程的计算基础是**人工费**（其余为直接工程费）。
INDIRECT_FEE_RATES: Dict[str, float] = {
    "土方工程": 5, "石方工程": 6, "砌体工程": 5, "混凝土工程": 6,
    "农用井工程": 8, "其他工程": 5, "安装工程": 65,
}

# 计划利润 / 税金（井神 §7.1.2 第 3）4）条：利润 =（直接费+间接费）×3%；税金 =（直接费+间接费+利润）×税率，税率 9%。
PROFIT_RATE = 3.0
TAX_RATE = 9.0

# 其他费用估算表（表7-4）—— 井神 / 王元佳苑 / 明远 **3 份公式逐字一致**。
# 每条：分组、名称、基（可引用已算出的分组小计/工程施工费/设备购置费）、费率（%）。
# 样本「其他费用估算表」的单元格**直接写着算式**（如 `=工程施工费×0.20%`），故这些费率是
# **照抄**而非臆造。拆迁补偿费无固定公式（样本据实为 0），不列规则。
OTHER_FEE_RULES: List[Dict[str, Any]] = [
    {"group": "前期工作费", "name": "土地清查费", "base": ["工程施工费"], "rate": 0.20},
    {"group": "前期工作费", "name": "项目可行性研究费", "base": ["工程施工费", "设备购置费"], "rate": 1.00},
    {"group": "前期工作费", "name": "项目勘测费", "base": ["工程施工费"], "rate": 0.50},
    {"group": "前期工作费", "name": "项目规划设计与预算编制费", "base": ["工程施工费", "设备购置费"], "rate": 2.40},
    {"group": "前期工作费", "name": "施工图设计费", "base": ["工程施工费", "设备购置费"], "rate": 1.20},
    {"group": "前期工作费", "name": "项目立项和规划设计及预算审查费", "base": ["工程施工费"], "rate": 0.70},
    {"group": "前期工作费", "name": "项目招标费", "base": ["工程施工费", "设备购置费"], "rate": 0.80},
    {"group": "工程监理费", "name": "工程监理费", "base": ["工程施工费", "设备购置费"], "rate": 2.00},
    {"group": "竣工验收费", "name": "项目竣工测量与工程复核费", "base": ["工程施工费", "设备购置费"], "rate": 1.00},
    {"group": "竣工验收费", "name": "工程验收费", "base": ["工程施工费", "设备购置费"], "rate": 1.20},
    {"group": "竣工验收费", "name": "项目决算编制与审计费", "base": ["工程施工费", "设备购置费"], "rate": 1.20},
    {"group": "竣工验收费", "name": "土地重估与登记费", "base": ["工程施工费", "设备购置费"], "rate": 0.60},
    {"group": "竣工验收费", "name": "标牌与标识设定费", "base": ["工程施工费", "设备购置费"], "rate": 0.20},
    {"group": "业主管理费", "name": "业主管理费",
     "base": ["工程施工费", "设备购置费", "前期工作费", "工程监理费", "拆迁补偿费", "竣工验收费"],
     "rate": 2.80},
]

# 投资估算表（表7-5）—— 王元佳苑 / 明远一致：
#   复垦监测费 = 工程施工费 × 1.5%；基本预备费 =（一+二+三+四）×6%；风险费 = 工程施工费 × 2%。
MONITORING_RATE = 1.5            # 复垦监测费费率（%），基数＝工程施工费
BASIC_RESERVE_RATE = 6.0         # 基本预备费费率（%），基数＝工程施工费+设备费+其他费用+监测管护费
RISK_RATE = 2.0                  # 风险金费率（%），基数＝工程施工费
PRICE_RISE_RATE = 5.0            # 价差预备费率（%），井神 §7.1.2 2）条「年度价格上涨水平 5.0%」
GUARD_SALARY_UNIT_PRICE = 1000.0  # 管护费单价 元/(人·月)，井神 §6.1.3/§7.2.4

# 适宜性评价因子权重（三级因子）—— 井神 / 污水处理厂两份样本的「三级影响因子表」逐字一致，
# 10 项权重之和 = 1.00。**分级标准列**（因素特征→分值）在本仓样本抽取里存在噪声
# （个别单元格分值缺、区间串字），故本工具不实现「特征→分值」的推导 —— 评价单元的
# 各因子**分值**（表10 口径）作为入参直接给。
SUITABILITY_WEIGHTS: Dict[str, float] = {
    "自然坡度": 0.10, "有效土层厚度": 0.11, "土壤质地": 0.12,
    "有机质含量": 0.12, "地貌部位": 0.10,
    "灌溉保证程度": 0.11, "灌溉方式": 0.08, "排水保证程度": 0.09,
    "道路通达性": 0.08, "田块平整": 0.09,
}
# 等别阈值（表9 备注：一等地 80—100；二等地 60-80；三等地〈60）—— 井神/污水处理厂一致。
GRADE_THRESHOLDS = [("一等地", 80.0), ("二等地", 60.0)]   # 依次回落；<60 为三等地


# ═══════════════════════════════════════════════════════════════
# 小工具
# ═══════════════════════════════════════════════════════════════

def _f(v, default: float = 0.0) -> float:
    """把入参强转成 float；None/空串/非法值 → default。"""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _model_json(tool: str, model, note: str = "") -> str:
    """把一本账本序列化成工具返回串（值 + 来源 + 算法 + 审计）。"""
    entries = [
        {
            "名称": name,
            "值": tv.value,
            "单位": tv.unit,
            "来源": tv.source,
            "算法": tv.formula,
        }
        for name, tv in model.items()
    ]
    payload: Dict[str, Any] = {"工具": tool, "数值": entries}
    issues = model.audit()
    if issues:
        payload["审计"] = issues
    if note:
        payload["说明"] = note
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# 工具 1：工程量测算
# ═══════════════════════════════════════════════════════════════
@register_tool(
    name="calc_earthwork",
    description=(
        "按复垦工程设计测算工程量：表土剥离量 = 耕地面积×剥离厚度、"
        "混凝土层工程量 = 功能区面积×混凝土层厚度、垫层工程量 = 功能区面积×垫层厚度、"
        "沟渠疏通量 = 沟渠面积×沟渠疏通系数。面积单位为 ㎡，厚度/系数按样本经验值可省。"
        "返回每个工程量的值、单位、算法与来源（可反查、可自洽校验）。"
        "当正文（第6章 工程量测算）需要工程量数据时调用。"
    ),
    domains=_RECLAMATION,
    parameters={
        "type": "object",
        "properties": {
            "soil_area_m2": {"type": "number", "description": "表土剥离面积（耕地面积），单位 ㎡"},
            "topsoil_thickness_m": {"type": "number", "description": "剥离表土厚度，单位 m（缺省 0.3，样本经验值）"},
            "functional_area_m2": {"type": "number", "description": "功能区面积（场地清理/硬化层范围），单位 ㎡"},
            "concrete_thickness_m": {"type": "number", "description": "混凝土层厚度，单位 m（缺省 0.10，样本经验值）"},
            "cushion_thickness_m": {"type": "number", "description": "垫层厚度，单位 m（缺省 0.08，样本经验值）"},
            "ditch_area_m2": {"type": "number", "description": "沟渠面积，单位 ㎡"},
            "ditch_coef": {"type": "number", "description": "沟渠疏通系数（缺省 1.5，样本经验值）"},
        },
        "required": [],
    },
)
async def _calc_earthwork(
    ctx: dict,
    soil_area_m2: float = 0,
    topsoil_thickness_m: float = None,
    functional_area_m2: float = 0,
    concrete_thickness_m: float = None,
    cushion_thickness_m: float = None,
    ditch_area_m2: float = 0,
    ditch_coef: float = None,
) -> str:
    """工程量测算：面积×厚度/系数 → 工程量（m³，附 百m³）。"""
    try:
        from app.services.reclamation_data_model import ReclamationDataModel
        m = ReclamationDataModel()
        src = "复垦工程设计（第6章）"

        # 表土剥离量：耕地面积 × 剥离厚度（井神：0.1936hm² 折 1936㎡ × 0.3m = 580.8m³）
        t_thick = 0.3 if topsoil_thickness_m is None else _f(topsoil_thickness_m)
        if _f(soil_area_m2) > 0:
            m.set_input("耕地面积", _f(soil_area_m2), unit="m2", source=src)
            m.set_input("剥离厚度", t_thick, unit="m", source=src + "（缺省取样本经验值 0.3m）")
            m.derive("表土剥离量", "耕地面积 * 剥离厚度", unit="m3")
            m.derive("表土剥离量_百m3", "表土剥离量 / 100", unit="百m3")

        # 混凝土层 / 垫层工程量：功能区面积 × 层厚（井神：2354㎡ × 0.10 = 235.4m³；× 0.08 = 188.32m³）
        c_thick = 0.10 if concrete_thickness_m is None else _f(concrete_thickness_m)
        s_thick = 0.08 if cushion_thickness_m is None else _f(cushion_thickness_m)
        if _f(functional_area_m2) > 0:
            m.set_input("功能区面积", _f(functional_area_m2), unit="m2", source=src)
            m.set_input("混凝土层厚度", c_thick, unit="m", source=src + "（缺省取样本经验值 0.10m）")
            m.set_input("垫层厚度", s_thick, unit="m", source=src + "（缺省取样本经验值 0.08m）")
            m.derive("混凝土层工程量", "功能区面积 * 混凝土层厚度", unit="m3")
            m.derive("混凝土层工程量_百m3", "混凝土层工程量 / 100", unit="百m3")
            m.derive("垫层工程量", "功能区面积 * 垫层厚度", unit="m3")
            m.derive("垫层工程量_百m3", "垫层工程量 / 100", unit="百m3")

        # 沟渠疏通量：沟渠面积 × 系数（井神：393㎡ × 1.5 = 589.5m³）
        d_coef = 1.5 if ditch_coef is None else _f(ditch_coef)
        if _f(ditch_area_m2) > 0:
            m.set_input("沟渠面积", _f(ditch_area_m2), unit="m2", source=src)
            m.set_input("沟渠疏通系数", d_coef, unit="", source=src + "（缺省取样本经验值 1.5）")
            m.derive("沟渠疏通量", "沟渠面积 * 沟渠疏通系数", unit="m3")
            m.derive("沟渠疏通量_百m3", "沟渠疏通量 / 100", unit="百m3")

        if not m.items():
            return json.dumps({"工具": "calc_earthwork",
                               "数值": [],
                               "说明": "未提供任何面积，无法测算。请至少给出 soil_area_m2 / "
                                       "functional_area_m2 / ditch_area_m2 之一。"},
                              ensure_ascii=False, indent=2)
        return _model_json("calc_earthwork", m)
    except Exception as e:
        logger.warning("calc_earthwork tool error: %s", e)
        return f"【工程量测算失败】{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════
# 工具 2：投资估算
# ═══════════════════════════════════════════════════════════════
@register_tool(
    name="calc_investment",
    description=(
        "土地复垦投资估算：由 工程施工费（可另给各工程类别直接工程费，按措施费/间接费费率表合成）"
        "算 其他费用（前期工作费/工程监理费/竣工验收费/业主管理费，各按『工程施工费/设备购置费』"
        "的一定费率）、监测与管护费、预备费（基本预备费/风险金/价差预备费），汇总静态/动态总投资，"
        "并可给出按年投资安排。费率取自样本报告，可按参数覆盖。金额单位：万元。"
        "返回每一项的值、算法与来源。当正文（第7章 投资估算）需要投资数据时调用。"
    ),
    domains=_RECLAMATION,
    parameters={
        "type": "object",
        "properties": {
            "engineering_cost": {"type": "number", "description": "工程施工费（万元）；若给 direct_costs 可省略"},
            "direct_costs": {
                "type": "object",
                "description": "各工程类别直接工程费（万元）→ 按措施费/间接费费率表合成工程施工费，"
                               "键如 土方工程/石方工程/砌体工程/混凝土工程/农用井工程/其他工程/安装工程",
            },
            "equipment_cost": {"type": "number", "description": "设备购置费（万元，缺省 0）"},
            "acceptance_fee": {"type": "number", "description": "复垦验收费（万元，据实，缺省 0）"},
            "monitoring_persons": {"type": "integer", "description": "管护人数（缺省 1）"},
            "monitoring_months": {"type": "integer", "description": "管护月数（缺省 6）"},
            "annual_static_investment": {
                "type": "array", "items": {"type": "number"},
                "description": "各年静态投资额（万元，按顺序＝第1、2…年），用于价差预备费；缺省则不计价差",
            },
        },
        "required": [],
    },
)
async def _calc_investment(
    ctx: dict,
    engineering_cost: float = None,
    direct_costs: dict = None,
    equipment_cost: float = 0,
    acceptance_fee: float = 0,
    monitoring_persons: int = 1,
    monitoring_months: int = 6,
    annual_static_investment: list = None,
) -> str:
    """投资估算：工程施工费 → 其他费用 → 监测管护费 → 预备费 → 静态/动态总投资。"""
    try:
        from app.services.reclamation_data_model import ReclamationDataModel
        m = ReclamationDataModel()
        src = "复垦投资估算（第7章，费率取自样本报告）"
        rate_src = "费率：样本报告 表7-4/表7-5"

        m.set_input("设备购置费", _f(equipment_cost), unit="万元", source="用户提供/据实")

        # ── ① 工程施工费：直接给，或由 直接工程费×（措施费+间接费+利润+税金）合成 ──
        if engineering_cost is not None and engineering_cost != "":
            m.set_input("工程施工费", _f(engineering_cost), unit="万元",
                        source="工程量×综合单价（第6章）")
        elif direct_costs:
            _synthesize_engineering_cost(m, direct_costs, src, rate_src)
        else:
            return json.dumps({"工具": "calc_investment", "数值": [],
                               "说明": "需给出 engineering_cost（工程施工费）或 direct_costs"
                                       "（各工程类别直接工程费）之一。"},
                              ensure_ascii=False, indent=2)

        # ── ② 其他费用：表7-4 的分组费率（业主管理费的基数含前面几组小计，故顺序求值）──
        _compute_other_fees(m, src, rate_src)

        # ── ③ 监测与管护费 ──
        m.derive("复垦监测费", f"工程施工费 * {MONITORING_RATE / 100}", unit="万元",
                 )
        monitor = m.get("复垦监测费")
        monitor.source = rate_src + f"：监测费率 {MONITORING_RATE}%×工程施工费"
        # 管护费 = 人数 × 月数 × 单价（元）→ 万元
        m.set_input("管护人数", _f(monitoring_persons, 1), unit="人", source="复垦管护安排")
        m.set_input("管护月数", _f(monitoring_months, 6), unit="月", source="复垦管护安排")
        m.set_input("管护单价", GUARD_SALARY_UNIT_PRICE, unit="元/(人·月)",
                    source="样本 §7.2.4：1000 元/(人·月)")
        m.derive("管护费", "管护人数 * 管护月数 * 管护单价 / 10000", unit="万元")
        if _f(acceptance_fee) > 0:
            m.set_input("复垦验收费", _f(acceptance_fee), unit="万元", source="用户提供/据实")
            m.derive("监测与管护费", "复垦监测费 + 管护费 + 复垦验收费", unit="万元")
        else:
            m.derive("监测与管护费", "复垦监测费 + 管护费", unit="万元")

        # ── ④ 预备费 ──
        # 基本预备费 =（工程施工费+设备购置费+其他费用+监测与管护费）×6%（表7-5 基数标注 [一+二+三+四]）
        m.derive("基本预备费",
                 f"(工程施工费 + 设备购置费 + 其他费用 + 监测与管护费) * {BASIC_RESERVE_RATE / 100}",
                 unit="万元")
        m.get("基本预备费").source = rate_src + f"：基本预备费率 {BASIC_RESERVE_RATE}%×（一+二+三+四）"
        # 风险金 = 工程施工费 × 2%
        m.derive("风险费", f"工程施工费 * {RISK_RATE / 100}", unit="万元")
        m.get("风险费").source = rate_src + f"：风险金费率 {RISK_RATE}%×工程施工费"

        # 静态总投资 = 工程施工费 + 设备购置费 + 其他费用 + 监测与管护费 + 基本预备费 + 风险费
        m.derive("静态总投资",
                 "工程施工费 + 设备购置费 + 其他费用 + 监测与管护费 + 基本预备费 + 风险费",
                 unit="万元")

        # ── ⑤ 价差预备费 & 动态总投资 ──
        # Wi = ai×((1+i)^ni − 1)（井神 §7.1.2 2）条，i = PRICE_RISE_RATE）。缺年度安排则不计入。
        note = ""
        if annual_static_investment:
            terms = []
            for k, a in enumerate(annual_static_investment, start=1):
                m.set_input(f"第{k}年静态投资", _f(a), unit="万元", source="复垦工作计划安排（第8章）")
                # 🔴 复利因子在 Python 里先算好、再写成字面量 —— 数据层引擎只做四则 + 括号，
                #    不支持 `**`（见 reclamation_data_model._OPS），算式里不能出现幂运算符。
                factor = round((1 + PRICE_RISE_RATE / 100) ** k - 1, 6)
                m.derive(f"第{k}年价差预备费", f"第{k}年静态投资 * {factor}", unit="万元")
                m.get(f"第{k}年价差预备费").source = (
                    rate_src + f"：Wi = 第{k}年静态投资×((1+{PRICE_RISE_RATE}%)^{k}−1)")
                terms.append(f"第{k}年价差预备费")
            m.derive("价差预备费", " + ".join(terms), unit="万元")
            m.derive("动态总投资", "静态总投资 + 价差预备费", unit="万元")
        else:
            note = ("未提供 annual_static_investment（各年静态投资额），价差预备费与动态总投资不计；"
                    "动态总投资 = 静态总投资 + 价差预备费，价差预备费按 Wi = ai×((1+i)^ni−1) 依年度安排计取。")

        payload = json.loads(_model_json("calc_investment", m, note))
        payload["静态总投资"] = m.get("静态总投资").value
        if "动态总投资" in dict(m.items()):
            payload["动态总投资"] = m.get("动态总投资").value
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("calc_investment tool error: %s", e)
        return f"【投资估算失败】{type(e).__name__}: {e}"


def _synthesize_engineering_cost(m, direct_costs: dict, src: str, rate_src: str) -> None:
    """由各工程类别『直接工程费』按表7-1/7-2 合成工程施工费。

    工程施工费 = 直接费 + 间接费 + 计划利润 + 税金；
    直接费 = Σ直接工程费 + Σ措施费；措施费、间接费按 表7-1/7-2 的分类费率计取。
    ⚠️ 表7-2 中「安装工程」的计算基础是**人工费**而非直接工程费 —— 本合成按直接工程费
       近似（并在来源里注明），需要精确时请直接给出 engineering_cost。
    """
    measure_total, indirect_total, direct_total = 0.0, 0.0, 0.0
    for cat, cost in direct_costs.items():
        c = _f(cost)
        if c <= 0:
            continue
        m.set_input(f"直接工程费_{cat}", c, unit="万元", source=src)
        d_total = sum(MEASURE_FEE_RATES.get(cat, {}).values())
        m.derive(f"措施费_{cat}", f"直接工程费_{cat} * {d_total / 100}", unit="万元")
        m.get(f"措施费_{cat}").source = rate_src + f" 表7-1：{cat} 措施费合计 {d_total}%"
        i_rate = INDIRECT_FEE_RATES.get(cat, 0)
        m.derive(f"间接费_{cat}", f"直接工程费_{cat} * {i_rate / 100}", unit="万元")
        m.get(f"间接费_{cat}").source = rate_src + f" 表7-2：{cat} 间接费 {i_rate}%"
        measure_total += c * d_total / 100
        indirect_total += c * i_rate / 100
        direct_total += c
    m.set_input("直接工程费合计", direct_total, unit="万元", source=src)
    m.set_input("措施费合计", measure_total, unit="万元", source=rate_src + " 表7-1")
    m.set_input("间接费合计", indirect_total, unit="万元", source=rate_src + " 表7-2")
    m.derive("直接费", "直接工程费合计 + 措施费合计", unit="万元")
    m.derive("计划利润", f"(直接费 + 间接费合计) * {PROFIT_RATE / 100}", unit="万元")
    m.derive("税金", f"(直接费 + 间接费合计 + 计划利润) * {TAX_RATE / 100}", unit="万元")
    m.derive("工程施工费", "直接费 + 间接费合计 + 计划利润 + 税金", unit="万元")


def _compute_other_fees(m, src: str, rate_src: str) -> None:
    """其他费用：按表7-4 的分组费率逐项计取；分组小计再进 业主管理费 的基数。

    `OTHER_FEE_RULES` 按分组连续排列、业主管理费在末组，故「离开一组即登记该组小计」；
    拆迁补偿费样本据实为 0，须在业主管理费之前先登记（其基数含此项）。
    """
    m.set_input("拆迁补偿费", 0.0, unit="万元", source=rate_src + "：样本据实为 0")
    _rule_names = {r["name"] for r in OTHER_FEE_RULES}
    cur_group, acc = None, 0.0
    for rule in OTHER_FEE_RULES:
        g = rule["group"]
        if g != cur_group:
            # 进入新分组前，把上一分组的小计落账（业主管理费自己的小计即「其他费用」前不落；
            # 单名分组如「工程监理费」其派生量本身就是小计，不覆盖）
            if cur_group and cur_group != "业主管理费" and cur_group not in _rule_names:
                m.set_input(cur_group, acc, unit="万元", source=rate_src + f"：{cur_group}小计")
            cur_group, acc = g, 0.0
        expr = " + ".join(rule["base"])
        m.derive(rule["name"], f"({expr}) * {rule['rate'] / 100}", unit="万元")
        m.get(rule["name"]).source = rate_src + f"：{rule['name']} = {expr}×{rule['rate']}%"
        acc += m.get(rule["name"]).value
    if cur_group and cur_group != "业主管理费" and cur_group not in _rule_names:
        m.set_input(cur_group, acc, unit="万元", source=rate_src + f"：{cur_group}小计")
    m.derive("其他费用",
             "前期工作费 + 工程监理费 + 拆迁补偿费 + 竣工验收费 + 业主管理费", unit="万元")


# ═══════════════════════════════════════════════════════════════
# 工具 3：适宜性评价
# ═══════════════════════════════════════════════════════════════
@register_tool(
    name="evaluate_suitability",
    description=(
        "按复垦适宜性评价因子权重计算评价单元的加权总分与等别（一等地≥80 / 二等地≥60 / 三等地<60），"
        "并据此判定复垦方向。入参给评价单元各三级因子的分值（0—100，表10 口径），"
        "权重缺省用样本报告的三级因子权重表。返回各因子加权贡献、加权总分、等别（可反查、可自洽校验）。"
        "当正文（第4章 土地复垦适宜性评价）需要评分与等别时调用。"
    ),
    domains=_RECLAMATION,
    parameters={
        "type": "object",
        "properties": {
            "unit_name": {"type": "string", "description": "评价单元名称，如『施工便道』"},
            "factor_scores": {
                "type": "object",
                "description": "各三级因子分值（0—100），键为因子名，如 自然坡度/有效土层厚度/土壤质地/"
                               "有机质含量/地貌部位/灌溉保证程度/灌溉方式/排水保证程度/道路通达性/田块平整",
            },
            "weights": {
                "type": "object",
                "description": "三级因子权重（可覆盖缺省的样本权重表）；键同 factor_scores",
            },
        },
        "required": ["factor_scores"],
    },
)
async def _evaluate_suitability(ctx: dict, unit_name: str = "", factor_scores: dict = None,
                                weights: dict = None) -> str:
    """适宜性评价：因子分值 × 因子权重 → 加权总分 → 等别。"""
    try:
        from app.services.reclamation_data_model import ReclamationDataModel
        factor_scores = factor_scores or {}
        if not factor_scores:
            return json.dumps({"工具": "evaluate_suitability", "数值": [],
                               "说明": "需给出 factor_scores（评价单元各三级因子分值）。"},
                              ensure_ascii=False, indent=2)

        # 权重：缺省用样本三级因子权重表；调用方可给 weights 覆盖
        std_weights = dict(weights) if weights else dict(SUITABILITY_WEIGHTS)

        m = ReclamationDataModel()
        unit = unit_name or "评价单元"
        default_src = "适宜性评价因子分值（表10 口径）"
        weight_src = ("因子权重：调用方提供" if weights
                      else "因子权重：样本报告 三级影响因子表（表9）")

        # 只对权重表里有的因子计权（多给的因子忽略，缺的因子报出）
        missing = [f for f in std_weights if f not in factor_scores]
        contrib_terms: List[str] = []
        for fname, w in std_weights.items():
            if fname not in factor_scores:
                continue
            m.set_input(fname, _f(factor_scores[fname]), unit="分", source=f"{unit}·{default_src}")
            cname = f"贡献_{fname}"
            m.derive(cname, f"{fname} * {_f(w)}", unit="分")
            m.get(cname).source = f"{weight_src}：{fname} 权重 {_f(w)}"
            contrib_terms.append(cname)

        if not contrib_terms:
            return json.dumps({"工具": "evaluate_suitability", "数值": [],
                               "说明": f"factor_scores 未能匹配任何标准因子；标准因子为："
                                       f"{'、'.join(SUITABILITY_WEIGHTS)}"},
                              ensure_ascii=False, indent=2)

        m.derive("加权总分", " + ".join(contrib_terms), unit="分")
        total = m.get("加权总分").value
        grade = _grade_of(total)

        note = f"评价单元「{unit}」加权总分 {total:g} 分 → {grade}。"
        if missing:
            note += f" ⚠️ 缺因子分值：{'、'.join(missing)}（未计入总分）。"
        payload = json.loads(_model_json("evaluate_suitability", m, note))
        payload["加权总分"] = total
        payload["等别"] = grade
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("evaluate_suitability tool error: %s", e)
        return f"【适宜性评价失败】{type(e).__name__}: {e}"


def _grade_of(total: float) -> str:
    """加权总分 → 等别（表9 备注：一等地 80—100；二等地 60-80；三等地〈60）。"""
    for name, lo in GRADE_THRESHOLDS:
        if total >= lo:
            return name
    return "三等地"
