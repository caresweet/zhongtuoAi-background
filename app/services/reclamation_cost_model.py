# -*- coding: utf-8 -*-
"""复垦投资估算的**费用模型与单价库**。

## 模型出处

**不是拍脑袋** —— 全部取自真实样本，并用样本自己的数字回验过：

    江苏盐业井神…土地复垦方案.docx
      表7-3 工程施工费估算表   → 13 个分项的综合单价
      表7-4 土地复垦投资情况表 → 费用构成 + 各项费率（带公式标记）

    明远路…土地复垦方案报告书（139 页）第 7 章 → 费用构成口径一致

## 费用模型（样本表7-4 原样）

    一 工程施工费  = Σ (分项工程量 × 综合单价)
    二 设备费
    三 其他费用    = 前期工作费 + 工程监理费 + 竣工验收费 + 业主管理费
    四 监测与管护费 = 复垦监测费 + 管护费 + 复垦验收费
        复垦监测费 = 工程施工费 × 1.5%
        管护费     = 1000 元/(人·月) × 人数 × 月数
    五 预备费      = 基本预备费 + 价差预备费 + 风险费
        基本预备费 = (一 + 二 + 三 + 四) × 6%
        风险费     = 工程施工费 × 2%
        价差预备费：年价格上涨率 5%
    六 静态总投资  = 一 + 二 + 三 + 四 + 基本预备费 + 风险费
    七 动态总投资  = 静态总投资 + 价差预备费

## 综合单价本身怎么来

单价 = 直接费 + 间接费 + 计划利润 + 税金；直接费 = 人工费 + 材料费 + 机械费 + 措施费。
其中**材料费 = Σ(定额材料消耗量 × 材料预算价格)**，
材料预算价格 =（材料原价 + 运杂费）×(1+场站运输损耗率)×(1+采购保管费率)，
材料原价取当地造价站发布的《建设工程材料信息价》。
**目前定额消耗量数据尚未录入**（见 docs/复垦投资估算-材料价格算法.md），
故本模块先用**样本实测的综合单价**做单价库 —— 样本单价本身就是按上面那条链算出来的，
可直接复用；等定额表录入后再把「算单价」这一层也打开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 单价库 —— 全部取自样本实测值（盐业井神表7-3 为主，明远第7章补充）
# ═══════════════════════════════════════════════════════════════
# 键 = 工程名称，值 = (综合单价(元), 计量单位, 出处)
UNIT_PRICES: Dict[str, Tuple[float, str, str]] = {
    # —— 土壤重构 ——
    "表土剥离":            (540.56,   "100m³", "盐业井神表7-3"),
    "表土回填":            (540.56,   "100m³", "盐业井神表7-3"),
    "田面平整":            (273.2,    "100m³", "盐业井神表7-3"),
    "土地翻耕":            (2902.21,  "公顷",  "盐业井神表7-3"),
    # —— 水土保持 / 恢复水利 ——
    "播撒草籽":            (508.21,   "公顷",  "盐业井神表7-3"),
    "袋装土加固编织袋土":   (204.64,   "100m³", "盐业井神表7-3"),
    "排水沟开挖":          (540.56,   "100m³", "盐业井神表7-3"),
    "沟渠疏通":            (1593.03,  "100m³", "盐业井神表7-3"),
    # —— 清理 ——
    "垫层清理":            (1008.57,  "100m³", "盐业井神表7-3"),
    "混凝土拆除":          (14294.34, "100m³", "盐业井神表7-3"),
    "垃圾外运工程":         (1840.84,  "100m³", "盐业井神表7-3"),
    # —— 生物化学 ——
    "土壤培肥（有机肥）":    (2500.0,   "吨",    "盐业井神表7-3"),
    "土壤培肥（复合肥）":    (3000.0,   "吨",    "盐业井神表7-3"),
}

# ═══════════════════════════════════════════════════════════════
# 费率 —— 样本表7-4 的「费率」列原样
# ═══════════════════════════════════════════════════════════════
RATE_MONITOR = 0.015       # 复垦监测费 = 工程施工费 × 1.5%
RATE_BASIC_RESERVE = 0.06  # 基本预备费 = (一+二+三+四) × 6%
RATE_RISK = 0.02           # 风险费     = 工程施工费 × 2%
RATE_PRICE_ESCALATION = 0.05   # 价差预备费：年价格上涨率 5%
CARE_FEE_PER_PERSON_MONTH = 1000.0   # 管护费 1000 元/(人·月)


@dataclass
class CostItem:
    """一个分项：工程名称 / 数量（按单价表的计量单位）。"""
    name: str
    quantity: float
    unit: str = ""
    unit_price: Optional[float] = None   # 不传则查单价库

    def resolve(self) -> Tuple[float, str]:
        if self.unit_price is not None:
            return float(self.unit_price), self.unit or ""
        hit = UNIT_PRICES.get(self.name)
        if not hit:
            raise KeyError(f"单价库里没有「{self.name}」，请显式给 unit_price")
        return hit[0], (self.unit or hit[1])


@dataclass
class CostEstimate:
    """估算结果（万元）。字段与样本表7-4 的条目一一对应。"""
    construction: float = 0.0          # 一 工程施工费
    equipment: float = 0.0             # 二 设备费
    other: float = 0.0                 # 三 其他费用
    monitor: float = 0.0               # 四(一) 复垦监测费
    care: float = 0.0                  # 四(二) 管护费
    acceptance: float = 0.0            # 四(三) 复垦验收费
    basic_reserve: float = 0.0         # 五(一) 基本预备费
    price_escalation: float = 0.0      # 五(二) 价差预备费
    risk: float = 0.0                  # 五(三) 风险费
    breakdown: List[Dict] = field(default_factory=list)   # 分项明细

    @property
    def monitor_care(self) -> float:
        """四 监测与管护费"""
        return self.monitor + self.care + self.acceptance

    @property
    def reserve(self) -> float:
        """五 预备费"""
        return self.basic_reserve + self.price_escalation + self.risk

    @property
    def static_total(self) -> float:
        """六 静态总投资 = 一 + 二 + 三 + 四 + 基本预备费 + 风险费（**不含价差预备费**）"""
        return (self.construction + self.equipment + self.other + self.monitor_care
                + self.basic_reserve + self.risk)

    @property
    def dynamic_total(self) -> float:
        """七 动态总投资 = 静态总投资 + 价差预备费"""
        return self.static_total + self.price_escalation


def _wan(yuan: float) -> float:
    """元 → 万元。"""
    return round(float(yuan) / 10000.0, 4)


def estimate(items: List[CostItem], *,
             equipment: float = 0.0,
             other: float = 0.0,
             care_people: int = 0, care_months: int = 0,
             acceptance_items: Optional[Dict[str, float]] = None,
             price_escalation: float = 0.0,
             monitor_rate: float = RATE_MONITOR,
             basic_reserve_rate: float = RATE_BASIC_RESERVE,
             risk_rate: float = RATE_RISK) -> CostEstimate:
    """按样本表7-4 的模型算一份投资估算。

    Args:
        items: 分项工程量清单。
        equipment / other: 设备费、其他费用（万元）。
        care_people / care_months: 管护人数与月数（管护费 = 1000 元/(人·月) × 人 × 月）。
        acceptance_items: 复垦验收费明细（万元），如 {"土壤污染监测": 4.2, ...}。
        price_escalation: 价差预备费（万元）—— 年度价格波动，按年测算后传入。
    """
    est = CostEstimate()
    for it in items:
        price, unit = it.resolve()
        # 单价表的单位多为「100m³」，数量按同单位给，直接相乘
        amount_wan = _wan(price * float(it.quantity))
        est.breakdown.append({"name": it.name, "unit": unit,
                              "quantity": it.quantity, "unit_price": price,
                              "amount_wan": amount_wan})
        est.construction += amount_wan
    est.construction = round(est.construction, 4)

    est.equipment = round(float(equipment), 4)
    est.other = round(float(other), 4)

    # 四 监测与管护费
    est.monitor = round(est.construction * monitor_rate, 4)
    est.care = round(_wan(CARE_FEE_PER_PERSON_MONTH * int(care_people) * int(care_months)), 4)
    est.acceptance = round(sum((acceptance_items or {}).values()), 4)

    # 五 预备费
    base = est.construction + est.equipment + est.other + est.monitor_care
    est.basic_reserve = round(base * basic_reserve_rate, 4)
    est.risk = round(est.construction * risk_rate, 4)
    est.price_escalation = round(float(price_escalation), 4)
    return est
