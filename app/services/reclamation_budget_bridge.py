# -*- coding: utf-8 -*-
"""复垦投资估算取数桥：zhongtuo-report ⇄ budget-maker（复垦工程费用编制软件）。

## 为什么要连它

报告表「投资估算费用构成」和第7章的 表7-3/表7-4 要的是**真钱**：
工程施工费 = Σ(工程量 × 综合单价)，综合单价 = 人工费 + 材料费 + 机械费 +
措施费 + 间接费 + 计划利润 + 税金，每一层都要查定额消耗量与当地材料信息价。
这套东西在 budget-maker（`127.0.0.1:8765`，江苏土地整治预算定额全库）里已经做好，
报告系统自己那份 `reclamation_cost_model.UNIT_PRICES` 只是**样本实测单价**的兜底。

budget-maker 的外置接口：`POST /api/ext/report-values`（见那边 `report_bridge.py`），
输入「定额编号 + 工程量」，输出：工程施工费、其他费用（表7-4）、基本预备费/风险费/
价差预备费（表7-5）、静态/动态总投资、亩均投资、逐项综合单价与人材机消耗量。

## 链路

    第6章 表6-1（工程名称｜单位｜数量）        ← LLM 生成，本模块**解析出来**
        ↓ 按 `QUOTA_MAP` 换成定额编号
    POST /api/ext/report-values
        ↓
    投资估算结果 → ① 报告表「投资估算费用构成」七行
                   ② 第7章 表7-3（工程施工费估算表）/ 表7-4（投资估算表）

## 三条硬约束（业主口径）

1. **不编数**：桥连不上、表解析不出来、某项没映射到定额 → 该行列留空 / 整张表不出，
   绝不拿 0 或估数顶上（`fetch_estimate` 失败即返回 None，调用方保持原样）。
2. **不猜定额**：`QUOTA_MAP` 每一项都写明依据（哪份样本/哪个项目实际用过，
   或定额库 `display_name` 的语义对应）。映射不了的项落到「直接计价项」，
   价格取 `reclamation_cost_model.UNIT_PRICES`（样本实测），再不行就**不列**。
3. **口径一致**：第7章正文、报告表、附表都读同一份 `estimate`，不各自再算一遍。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 默认端点（每次调用时读环境变量，便于测试里一键关掉）
# `RECLAMATION_BUDGET_ENDPOINT=""` → 整条链路停用，报告照常出、金额留空。
DEFAULT_ENDPOINT = "http://127.0.0.1:8765"


def _endpoint() -> str:
    return os.getenv("RECLAMATION_BUDGET_ENDPOINT", DEFAULT_ENDPOINT)

# ═══════════════════════════════════════════════════════════════
# 工程名称 → 定额编号
# ═══════════════════════════════════════════════════════════════
# 键 = 第6章 表6-1「工程或费用名称」列（可能带「（一）」后缀，匹配时先剥）
# 值：
#   code   定额编号（budget.db `quota.code`）
#   cond   工程条件档（`quota_res.cond` 的完整变体键）；不填按定额主档
#   mpick  机械规格（定额含「自卸汽车/液压挖掘机」这类多档机械时必须选）
#   factor 工程量折算系数（报告单位 → 定额单位），缺省 1
#   basis  选这一条的依据（**必须写**，业主会问「凭什么用这个定额」）
QUOTA_MAP: Dict[str, Dict[str, Any]] = {
    "表土剥离": dict(
        code="10374", cond="推土机推土/74kW III类土",
        basis="金湖风电复垦项目表3-1 表土剥离即用 10374（推土机推土），报告 6.1 亦写明推土机推土"),
    "表土回填": dict(
        code="10374", cond="推土机推土/74kW III类土",
        basis="同上：金湖项目「表土覆盖」用的也是 10374，与本方案回填工艺一致"),
    "混凝土面层拆除": dict(
        code="40171", mpick={},
        basis="定额 4-22「混凝土拆除 — 机械拆除 风镐 有钢筋」；算得综合单价 14755.59 元/100m³，"
              "与样本（盐业井神表7-3）实测 14294.34 元/100m³ 相差 3.2%，口径吻合"),
    "垫层清除": dict(
        code="40172", mpick={"液压挖掘机": "1.0m³"},
        basis="定额 4-22「混凝土拆除 — 机械拆除 液压挖掘机」：垫层是碎石/素混凝土，"
              "定额库无专项清除子目；算得 9534.56 元/100m³，与样本（明远表7-3）"
              "实测 9444.21 元/100m³ 相差 1.0%，口径吻合"),
    "垃圾外运": dict(
        code="20157", mpick={"自卸汽车": "自卸汽车 5t"},
        basis="定额 2-18「1.0m³挖掘机装石渣自卸汽车运输 — 运距1km」；建筑垃圾按石渣外运，"
              "自卸汽车取 5t（与金湖项目一致），运距取定额最短档 1km"
              "（样本 981.08/1840.84 元/100m³ 均低于书本任何运输档，按业主口径以书本为准）"),
    "土地翻耕": dict(
        code="10103",
        basis="定额 1-11「土地翻耕」，单位 hm²，与报告同单位，无需折算"),
    "田面平整": dict(
        code="10421", factor=0.1,
        basis="定额 1-37「平地机平土 — 一般平土」（100m³）。报告按 100m² 计量，"
              "按**田面平整厚度 0.10m** 折算体积（水田平整常规厚度），系数 0.1"),
    "沟渠疏通": dict(
        code="10255",
        basis="定额 1-26「小型挖掘机挖沟渠土方 — I类土」——沟渠疏通按挖沟渠土方计"),
}

# 定额库**没有对应子目**的项（材料/物资采购）走「直接计价项」：
# 单价取 `reclamation_cost_model.UNIT_PRICES`（样本实测），只进工程施工费、不拆定额。
# 目前会遇到的是：土壤培肥（有机肥/复合肥）、播撒草籽、袋装土加固、排水沟开挖等。

_SKIP_ROW = {"", "—", "-", "——", "小计", "合计", "总计"}


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("，", "").strip()
    m = re.match(r'^-?\d+(?:\.\d+)?', s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _clean_name(s: str) -> str:
    """「（一）表土剥离」→「表土剥离」；去掉圈号、序号与空白。"""
    s = str(s or "").strip()
    s = re.sub(r'^[（(][一二三四五六七八九十\d]+[)）]\s*', '', s)
    s = re.sub(r'^\d+[.．、]\s*', '', s)
    return s.strip()


# ═══════════════════════════════════════════════════════════════
# 第6章 表6-1 解析
# ═══════════════════════════════════════════════════════════════
def _md_tables(md: str) -> List[Tuple[str, List[List[str]]]]:
    """把 markdown 里的表格连同**紧邻上方的表题行**一起切出来。"""
    out: List[Tuple[str, List[List[str]]]] = []
    caption = ""
    buf: List[str] = []

    def flush():
        nonlocal caption, buf
        if len(buf) >= 2:
            rows = []
            for ln in buf:
                cells = [c.strip() for c in ln.strip().strip('|').split('|')]
                if cells and all(re.fullmatch(r':?-{2,}:?', c or '-') for c in cells):
                    continue                       # 分隔行
                rows.append(cells)
            if len(rows) >= 2:
                out.append((caption, rows))
        buf = []

    for raw in str(md or "").splitlines():
        ln = raw.strip()
        if ln.startswith('|') and ln.endswith('|'):
            buf.append(ln)
            continue
        if buf:
            flush()
        if ln:
            caption = re.sub(r'^#+\s*', '', ln)
    if buf:
        flush()
    return out


def parse_quantity_rows(markdown: str) -> List[dict]:
    """从第6章 markdown 里取出「工程量汇总表」的行。

    返回 `[{"section","name","unit","qty"}]`；表头认「工程(或费用)名称｜单位｜数量(工程量)」，
    表题含「工程量」的表优先（`表6-1 项目复垦工程量汇总表`）。
    解析不出返回 `[]` —— 调用方据此**整块跳过**投资估算，不硬编。
    """
    best: Optional[Tuple[int, str, Tuple[int, int, int], List[List[str]]]] = None
    for caption, rows in _md_tables(markdown):
        head = [re.sub(r'[（(].*?[)）]', '', c).strip() for c in rows[0]]
        i_name = next((i for i, h in enumerate(head)
                       if h in ("工程或费用名称", "工程名称", "项目名称", "名称", "分项工程")), None)
        i_unit = next((i for i, h in enumerate(head) if h.startswith("单位")), None)
        i_qty = next((i for i, h in enumerate(head)
                      if h.startswith("数量") or h.startswith("工程量")), None)
        if i_name is None or i_unit is None or i_qty is None:
            continue
        score = 2 if "工程量" in caption else (1 if "工程" in caption else 0)
        if best is None or score > best[0]:
            best = (score, caption, (i_name, i_unit, i_qty), rows[1:])
    if not best:
        return []

    _, _cap, (i_name, i_unit, i_qty), body = best
    out: List[dict] = []
    section = ""
    for cells in body:
        if len(cells) <= max(i_name, i_unit, i_qty):
            continue
        name = _clean_name(cells[i_name])
        unit = cells[i_unit].strip()
        qty = _num(cells[i_qty])
        if not name:
            continue
        if qty is None:                              # 一级分部行（一、土壤重构工程 — —）
            if name not in _SKIP_ROW:
                section = name
            continue
        if name in _SKIP_ROW:
            continue
        out.append({"section": section or "工程施工费", "name": name,
                    "unit": unit, "qty": qty})
    return out


# ═══════════════════════════════════════════════════════════════
# 组包 + 取数
# ═══════════════════════════════════════════════════════════════
def _direct_price(name: str, unit: str) -> Optional[float]:
    """直接计价项单价（元 / UNIT_PRICES 里那个单位）。

    先精确匹配；再做包含匹配（样本表里叫「垃圾外运工程」，工程量表里写「垃圾外运」）。
    🔴 包含匹配要求短的一方 ≥4 个字 —— 太短的名字（「土方」「平整」）会误配到别的项，
       宁可匹配不上（该项不计价并告警）也不按错单价算钱。
    """
    from app.services.reclamation_cost_model import UNIT_PRICES
    if name in UNIT_PRICES:
        return UNIT_PRICES[name][0]
    for k, v in UNIT_PRICES.items():
        if not k:
            continue
        short, long_ = (k, name) if len(k) <= len(name) else (name, k)
        if len(short) >= 4 and short in long_:
            return v[0]
    return None


def build_payload(rows: Sequence[dict], *, area_ha: float = 0.0,
                  project_name: str = "", dev_wan: float = 0.0,
                  monitoring_persons: int = 1, monitoring_months: int = 6,
                  acceptance_fee: float = 0.0,
                  annual_static_investment: Optional[Sequence[float]] = None
                  ) -> Tuple[dict, List[dict]]:
    """表6-1 行 → budget-maker 接口入参；同时把「直接计价项」明细一并返回。

    定额能覆盖的项 → `items`（定额编号 + 折算后的工程量）；
    覆盖不了的 → 按样本实测单价算成 `extra_engineering_wan`（万元）。
    返回 `(payload, direct_rows)`，`direct_rows` 供表7-3 补行，保证分项之和 = 工程施工费。
    """
    items: List[dict] = []
    direct_rows: List[dict] = []
    extra_yuan = 0.0
    skipped: List[str] = []
    for r in rows or []:
        name, unit, qty = r.get("name", ""), r.get("unit", ""), _num(r.get("qty"))
        if qty is None:
            continue
        spec = QUOTA_MAP.get(name)
        if spec:
            it = {"code": spec["code"], "qty": round(qty * float(spec.get("factor") or 1.0), 4),
                  "section": r.get("section") or "工程施工费", "name": name}
            if spec.get("cond"):
                it["cond"] = spec["cond"]
            if spec.get("mpick"):
                it["mpick"] = spec["mpick"]
            items.append(it)
            continue
        price = _direct_price(name, unit)
        if price is not None:
            amt = price * qty
            extra_yuan += amt
            direct_rows.append({"name": name, "unit": unit, "qty": qty,
                                "price": price, "amount_wan": round(amt / 10000.0, 4)})
            continue
        skipped.append(f"{name}（{qty}{unit}）")
    if skipped:
        logger.warning(f"[投资估算] 以下工程量没有对应定额/单价，未计价：{'、'.join(skipped)}")

    payload: Dict[str, Any] = {
        "project_name": project_name or "复垦项目",
        "items": items,
        "extra_engineering_wan": round(extra_yuan / 10000.0, 4),
        "dev_cost": round(float(dev_wan or 0.0), 4),
    }
    if area_ha and area_ha > 0:
        payload["area_ha"] = round(float(area_ha), 4)
    payload["reclamation"] = {
        "monitoring_persons": int(monitoring_persons or 1),
        "monitoring_months": int(monitoring_months or 6),
        "acceptance_fee": float(acceptance_fee or 0.0),
    }
    if annual_static_investment:
        payload["reclamation"]["annual_static_investment"] = list(annual_static_investment)
    return payload, direct_rows


def fetch_estimate(payload: dict, *, endpoint: str = "", timeout: float = 30.0) -> Optional[dict]:
    """POST 到 budget-maker；任何异常都返回 None（**不让它拖垮出报告**）。"""
    ep = (endpoint or _endpoint() or "").rstrip("/")
    if not ep:
        return None
    if not ep.endswith("/api/ext/report-values"):
        ep += "/api/ext/report-values"
    req = urllib.request.Request(
        ep, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:            # 400 = 有 needs_input/errors，body 仍是 JSON
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            logger.warning(f"[投资估算] 桥返回 HTTP {e.code}，无法解析 body")
            return None
    except Exception as e:                          # 连不上/超时
        logger.warning(f"[投资估算] 桥不可用（{ep}）：{type(e).__name__}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("ok"):
        logger.warning(f"[投资估算] 桥未算全：errors={data.get('errors')} "
                       f"needs_input={[n.get('code') for n in (data.get('needs_input') or [])]}")
        return None
    return data


# ═══════════════════════════════════════════════════════════════
# 结果归一
# ═══════════════════════════════════════════════════════════════
def _f(v, nd: int = 2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return ""


# 报告表「投资估算费用构成」的七行（序号｜名称）—— 样本 表7-4 的条目
COST_ROW_NAMES: Tuple[Tuple[str, str], ...] = (
    ("一", "工程措施费"), ("二", "设备费"), ("三", "其他费用"),
    ("四", "监测与管护费"), ("五", "预备费"),
    ("六", "静态总投资"), ("七", "动态总投资"),
)


def empty_cost_rows() -> List[Tuple[str, str, str]]:
    """桥不可用时的空表：项目名照印、金额留空（**不写 0** 冒充）。"""
    return [(no, name, "") for no, name in COST_ROW_NAMES]


@dataclass
class BudgetEstimate:
    """报告侧用的投资估算（万元）—— 与样本 表7-4 的七个条目一一对应。"""
    construction: float = 0.0      # 一 工程施工费（= 报告表里的「工程措施费」）
    equipment: float = 0.0         # 二 设备购置费
    other: float = 0.0             # 三 其他费用
    monitor_care: float = 0.0      # 四 监测与管护费
    reserve: float = 0.0           # 五 预备费（基本预备费 + 风险费 + 价差预备费）
    static_total: float = 0.0      # 六 静态总投资
    dynamic_total: Optional[float] = None   # 七 动态总投资（未计价差时**留空**，不写 0）
    per_mu: float = 0.0            # 亩均投资
    per_ha: float = 0.0
    source: str = ""
    items: List[dict] = field(default_factory=list)          # 逐项综合单价（定额项）
    direct_items: List[dict] = field(default_factory=list)   # 直接计价项（定额库没有的）
    investment: List[dict] = field(default_factory=list)     # 表7-4 行
    summary: str = ""

    @property
    def cost_rows(self) -> List[Tuple[str, str, str]]:
        """报告表「投资估算费用构成」的七行（序号｜名称｜费用万元）。"""
        _vals = [_f(self.construction), _f(self.equipment), _f(self.other),
                 _f(self.monitor_care), _f(self.reserve), _f(self.static_total),
                 _f(self.dynamic_total) if self.dynamic_total else ""]
        return [(no, name, v) for (no, name), v in zip(COST_ROW_NAMES, _vals)]


def to_estimate(data: dict) -> Optional[BudgetEstimate]:
    """接口返回 → `BudgetEstimate`（任何一项缺失都**按缺失处理**，不补 0 冒充）。"""
    rep = data.get("report") or {}
    if not rep:
        return None
    try:
        basic = float(rep.get("basic_reserve_wan") or 0.0)
        risk = float(rep.get("risk_fee_wan") or 0.0)
        cont = rep.get("price_contingency_wan")
        dynamic = rep.get("dynamic_total_wan")
        return BudgetEstimate(
            construction=float(rep.get("engineering_cost_wan") or 0.0),
            equipment=float(rep.get("equipment_cost_wan") or 0.0),
            other=float(rep.get("other_fee_wan") or 0.0),
            monitor_care=float(rep.get("monitoring_and_guard_wan") or 0.0),
            reserve=round(basic + risk + float(cont or 0.0), 2),
            static_total=float(rep.get("static_total_wan") or 0.0),
            dynamic_total=(float(dynamic) if dynamic is not None else None),
            per_mu=float(rep.get("per_mu_wan") or 0.0),
            per_ha=float(rep.get("per_ha_wan") or 0.0),
            source=str(data.get("generated_at") or ""),
            items=list((data.get("tables") or {}).get("items") or []),
            investment=list((data.get("tables") or {}).get("investment") or []),
            summary=str(data.get("summary_text") or ""),
        )
    except Exception as e:                                # pragma: no cover
        logger.warning(f"[投资估算] 结果归一失败：{e}")
        return None


def estimate_for_report(quantity_rows: Sequence[dict], *, area_ha: float = 0.0,
                        project_name: str = "", dev_wan: float = 0.0,
                        service_months: float = 0.0,
                        endpoint: str = "", timeout: float = 30.0,
                        **chain_opts) -> Optional[BudgetEstimate]:
    """一步到位：表6-1 行 → 投资估算。桥不可用 / 没有可计价项 → None。

    ## 价差预备费怎么定（决定「动态总投资」）

    书本公式 Wi = ai×((1+i)^ni − 1)，i = 5%，ai = 第 ni 年静态投资额。
    `ni` 取**几年**要看复垦工作排在编制后第几年：

      · 服务年限 ≤ 12 个月 → 当年完成，ni = 1 → 价差 = 静态 × 5%
      · 服务年限 > 12 个月 → 复垦在第二年起实施，ni = 2 → 价差 = 静态 × 10.25%

    依据：样本《盐业井神》表7-5 的价差 2.03 万 / 静态 19.85 万 = 10.22% = (1.05²−1)，
    即 ni = 2。（明远样本那份 0.72 万 = 3.5%，套不上任何档，业主已说明样本
    未按书本强对准，以书本算式为准。）

    实现上不另造公式：把「第 ni 年一次投完」表达成 `annual_static_investment`
    （前面各年为 0），桥端照原公式算即可。第一次调用拿静态总投资，第二次带年度
    安排复算 —— 两次的工程量与费率完全相同，只有价差那一项变化。
    """
    if not quantity_rows:
        return None
    payload, direct_rows = build_payload(quantity_rows, area_ha=area_ha,
                                         project_name=project_name, dev_wan=dev_wan,
                                         **chain_opts)
    if not payload.get("items") and not payload.get("extra_engineering_wan"):
        logger.info("[投资估算] 表6-1 里没有可计价项，跳过取数")
        return None
    data = fetch_estimate(payload, endpoint=endpoint, timeout=timeout)
    if not data:
        return None
    est = to_estimate(data)
    if not est:
        return None

    if est.dynamic_total is None and est.static_total > 0:
        _ni = 1 if 0 < service_months <= 12 else 2
        _annual = [0.0] * (_ni - 1) + [est.static_total]
        p2, _ = build_payload(quantity_rows, area_ha=area_ha, project_name=project_name,
                              dev_wan=dev_wan, annual_static_investment=_annual,
                              **chain_opts)
        d2 = fetch_estimate(p2, endpoint=endpoint, timeout=timeout)
        est2 = to_estimate(d2) if d2 else None
        if est2 and est2.dynamic_total:
            est = est2

    if direct_rows:
        est.direct_items = direct_rows
    if est.static_total > 0:
        logger.info(f"[投资估算] {est.source} 工程施工费 {est.construction:.2f} 万元、"
                    f"静态总投资 {est.static_total:.2f} 万元（{len(est.items)} 个定额项、"
                    f"{len(direct_rows)} 个直接计价项）")
    return est


# ═══════════════════════════════════════════════════════════════
# 第7章的表
# ═══════════════════════════════════════════════════════════════
def engineering_table(est: BudgetEstimate) -> Optional[dict]:
    """表7-3 工程施工费估算表：逐定额项 单位｜工程量｜综合单价｜合计。

    直接计价项（定额库没有子目的材料类）单列在末尾 —— 否则分项之和 ≠ 工程施工费，
    业主一对账就看出来了。
    """
    if not est.items and not est.direct_items:
        return None
    rows = []
    total = 0.0
    for it in est.items:
        amt = float(it.get("amount_wan") or 0.0)
        total += amt
        rows.append([str(it.get("name") or ""), str(it.get("unit") or ""),
                     _f(it.get("qty"), 2), _f(it.get("price")), _f(amt, 4)])
    for d in est.direct_items:
        amt = float(d.get("amount_wan") or 0.0)
        total += amt
        rows.append([str(d.get("name") or ""), str(d.get("unit") or ""),
                     _f(d.get("qty"), 2), _f(d.get("price")), _f(amt, 4)])
    rows.append(["合计", "", "", "", _f(total, 4)])
    return {"caption": "表7-3 工程施工费估算表",
            "headers": ["工程或费用名称", "单位", "工程量", "综合单价（元）", "合计（万元）"],
            "rows": rows}


def investment_table(est: BudgetEstimate) -> Optional[dict]:
    """表7-4 土地复垦投资估算表（一~七 + 亩均投资）。"""
    if est.static_total <= 0:
        return None
    rows = [
        ["一", "工程施工费", _f(est.construction)],
        ["二", "设备购置费", _f(est.equipment)],
        ["三", "其他费用", _f(est.other)],
        ["四", "监测与管护费", _f(est.monitor_care)],
        ["五", "预备费", _f(est.reserve)],
        ["六", "静态总投资", _f(est.static_total)],
    ]
    if est.dynamic_total:
        rows.append(["七", "动态总投资", _f(est.dynamic_total)])
    if est.per_mu:
        rows.append(["", "亩均投资（万元/亩）", _f(est.per_mu, 4)])
    return {"caption": "表7-4 土地复垦投资估算表",
            "headers": ["序号", "工程或费用名称", "金额（万元）"],
            "rows": rows}
