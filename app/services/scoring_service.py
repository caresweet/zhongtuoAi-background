"""
Data-driven scoring service for stability risk assessment reports.

**口径完全对齐参照件《白马湖农场2号风电项目社会稳定风险评估报告》第十二章：**

  权重：合法性 10 + 合理性 25 + 可行性 10 + 可控性 55 = 100（扣分制）
  群众意见分析（权重 35）：每行得分 = a × b × 35
      a = 反对率百分比（0.075 表示 7.5%）
      b = 风险发生概率或激烈程度系数
          肯定/有较大可能发生/强烈反对 → 1.0
          有可能发生/反对较强烈         → 0.7
          发生概率很小/激烈程度一般     → 0.3
      群众意见分析得分 = Σ(各行 a×b) × 35
  总分 W = 各项得分之和

  风险等级判定（两步）：
    ① 直接定性指标（表12-1）：超 50% 群众反对 / 可能引发 200 人以上群体性事件 /
       恶性案件 / 个人极端事件 → 高风险；
       20%–50% 反对 / 可能引发 100 人以上群体性事件 → 中风险。
       仅符合一项中风险情形 → 中风险；其他情形 → 高风险。
    ② 不符合任一情形时按 W 判：W ≥ 70 高风险；40 < W < 70 中风险；W ≤ 40 低风险。

**分工**：本模块只负责**算数与判定**，并给出每项的**评分标准原文**（与项目无关）。
每项的「扣分依据」文字**由章节 agent 依本项目事实卡撰写**（`basis` 留空 + `basis_hint` 提示），
本模块内**不写任何项目专属文字**（旧版写死了「征收主体」「土地管理法第四十六条」等征地话术，
换成风电/道路项目就全错）。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple


# ═══════════════════════════════════════════════════════════════
# 量化指标评价体系骨架（权重与评分标准取自标准原文，与项目无关）
#   (维度, 权重, 测评项目, 最高扣分, 评分标准, 依据撰写提示)
# ═══════════════════════════════════════════════════════════════
SCORING_SKELETON: List[Tuple[str, int, str, int, str, str]] = [
    # ── 合法性（权重 10）──
    ("合法性", 10, "实施主体是否符合国家法律法规和规章的相关规定", 3,
     "全部符合的不计分，基本符合计2分，不符合的计3分",
     "写明本项目实施/建设主体是谁、依据哪部法律法规取得何种资格"),
    ("合法性", 10, "内容是否符合国家的法律法规和规章；是否符合党和国家的路线方针政策，法定前置要件是否齐全", 5,
     "全部符合的不计分，基本符合计2分，不符合的计5分",
     "写明本项目已履行/取得的法定前置要件（审批、备案、规划、用地等）及对应依据"),
    ("合法性", 10, "决策程序是否符合规定的议事决策规定", 2,
     "符合的不计分，不符合的计2分",
     "写明本项目立项/决策所遵循的议事决策程序及依据"),

    # ── 合理性（权重 25）──
    ("合理性", 25, "所涉及的利益相关方的界定是否明确", 2,
     "明确的不计分，基本明确的计1分，不明确的计2分",
     "写明本项目涉及的利益相关方范围及界定依据（权属/利益关系）"),
    ("合理性", 25, "对利益相关方的信息公开是否到位", 2,
     "到位的不计分，不到位的计2分",
     "写明本项目信息公开的方式、载体与覆盖情况（取事实卡真实数据）"),
    ("合理性", 25, "群众满意度测评是否达标", 6,
     "满意度85％以上的不计分，70-85％计4分，低于70%的计6分",
     "取事实卡真实满意度/支持率并写明问卷口径"),
    ("合理性", 25, "会不会引发不同地区、行业、群体之间的攀比", 4,
     "引发攀比可能性较小的不计分，有可能引发攀比的计2分，引发攀比可能性较大的计4分",
     "写明本项目补偿/安置口径与周边同类项目是否一致"),
    ("合理性", 25, "专业论证是否可行", 3,
     "可行的不计分，不可行的计2分",
     "写明本项目已完成的专业论证工作（勘测、设计、评审等）"),
    ("合理性", 25, "对所涉及群众的补偿、安置、保障等措施是否到位", 4,
     "到位的不计分，基本到位的计2分，不到位的计4分",
     "写明本项目补偿安置与社会保障安排及其依据文件"),
    ("合理性", 25, "基层党委政府是否支持", 4,
     "支持的不计分，无所谓的计2分，不支持的计4分",
     "写明基层组织/属地政府对本项目的意见（取调查资料）"),

    # ── 可行性（权重 10）──
    ("可行性", 10, "现有财政经济实力是否可以支撑相关成本支出", 3,
     "可以支撑的不计分，不能支撑的计3分",
     "写明本项目资金来源与落实方式（取事实卡）"),
    ("可行性", 10, "对环境影响情况", 4,
     "不涉及环境影响的不计分，有影响的计2分，涉邻避项目的计4分",
     "写明本项目对环境的影响类型与程度，并据此判分"),
    ("可行性", 10, "现有技术条件是否具备", 3,
     "具备的不计分，基本具备的计1分，不具备的计3分",
     "写明本项目所需技术条件的具备情况"),

    # ── 可控性（权重 55）──
    ("可控性", 55, "群众意见分析", 35,
     "按表12-3计算：各群体反对率 a 与风险发生概率/激烈程度系数 b 相乘后求和，再乘以 35",
     "由系统按公式自动计算；写明各行 a、b 的取值依据"),
    ("可控性", 55, "负面舆论", 3,
     "无负面舆论的不计分，有负面舆论计1分，负面舆论较大的计3分",
     "写明本项目舆情监测情况；无确凿证据的敏感项按不扣分处理并注明待评审"),
    ("可控性", 55, "恶意炒作", 3,
     "不会引发恶意炒作的不计分，可能引发恶意炒作计1分，较大可能引发的计3分",
     "写明是否存在被恶意炒作的情形；敏感项无证据不扣分并注明待评审"),
    ("可控性", 55, "维权人士插手", 3,
     "无维权人士插手的不计分，有维权人士插手计1分，插手较多的计3分",
     "写明基层调研结论；敏感项无证据不扣分并注明待评审"),
    ("可控性", 55, "敌对组织、敌对势力插手", 3,
     "无敌对组织、敌对势力插手的不计分，有插手的计3分",
     "写明有无相关情报"),
    ("可控性", 55, "是否建立不稳定因素台账和报告制度", 3,
     "建立的不计分，已建立但不完善的计1分，未建立的计3分",
     "写明本项目/本单位不稳定因素台账与报告制度的建立情况"),
    ("可控性", 55, "风险防范化解预案是否详实完整", 2,
     "详实完整的不计分，基本完整的计1分，不完整的计2分",
     "写明本项目应急预案的详实程度（对应第5章）"),
    ("可控性", 55, "宣传解释和舆论引导工作是否到位", 3,
     "到位的不计分，基本到位的计1分，不到位的计3分",
     "写明本项目宣传解释与舆论引导工作的开展情况"),
]

# 敏感项（无确凿证据时不扣分，标注待专家评审）
SENSITIVE_ITEMS = {"负面舆论", "恶意炒作", "维权人士插手"}

# 风险等级阈值（参照件 第十二章 注 1.3）
THRESHOLD_HIGH = 70    # W ≥ 70 → 高风险
THRESHOLD_MID = 40     # 40 < W < 70 → 中风险；W ≤ 40 → 低风险

# 直接定性指标（参照件 表12-1）
# 🔴 用**元组**而不是集合：集合无序，解包会把字段名/运算符/阈值错位
QUALITATIVE_INDICATORS = [
    (("群众反对率", "gt", 50), "高风险", "超过50%以上群众反对的"),
    (("群体性事件人数", "ge", 200), "高风险", "可能引发200人以上群体性事件的"),
    (("恶性案件", "eq", True), "高风险", "可能引发恶性案件"),
    (("个人极端事件", "eq", True), "高风险", "可能引发个人极端事件"),
    (("群众反对率", "between", (20, 50)), "中风险", "20%以上～50%以下群众反对的"),
    (("群体性事件人数", "ge", 100), "中风险", "可能引发100人以上群体性事件的"),
]


@dataclass
class ScoringItem:
    category: str
    weight: int
    item: str
    max_score: int
    criteria: str
    score: Optional[int] = None       # None = 由章节 agent 依事实判定
    basis: str = ""                   # 扣分依据（章节 agent 撰写）
    basis_hint: str = ""              # 撰写提示（告诉 agent 该写什么）
    is_auto: bool = False             # True = 系统按公式/事实自动算出


# ═══════════════════════════════════════════════════════════════
# 群众意见分析（表12-3）—— 参照件公式
# ═══════════════════════════════════════════════════════════════

# 风险发生概率 / 激烈程度 → 系数 b
B_COEFFICIENT = {
    "strong": 1.0,     # 肯定，有较大可能发生的 / 表示强烈反对的
    "likely": 0.7,     # 有可能发生的 / 反对较强烈的
    "unlikely": 0.3,   # 发生概率很小的 / 反对激烈程度一般的
}


def compute_public_opinion(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按参照件公式计算「群众意见分析」得分（表12-3）。

    Args:
        rows: [{"a": 0.075, "b": 0.7, "note": "…"}, ...]
              a = 反对率百分比（0.075 = 7.5%）；b = 概率/激烈程度系数。

    Returns:
        {"rows": [{a, b, score}], "total": Σ(a×b)×35}
    """
    out, total = [], 0.0
    for r in (rows or []):
        try:
            a = float(r.get("a", 0) or 0)
            b = float(r.get("b", 0) or 0)
        except (TypeError, ValueError):
            continue
        s = a * b * 35.0
        total += s
        out.append({"a": a, "b": b, "note": r.get("note", ""), "score": round(s, 2)})
    return {"rows": out, "total": round(total, 2)}


def rows_from_oppose_rate(oppose_rate_pct: float) -> List[Dict[str, Any]]:
    """由单一反对率推一个默认行（b 取「有可能发生/反对较强烈」= 0.7）。

    章节 agent 可依本项目实际（分群体的反对率与激烈程度）改写这张表。
    """
    try:
        a = float(oppose_rate_pct or 0) / 100.0
    except (TypeError, ValueError):
        a = 0.0
    return [{"a": round(a, 4), "b": B_COEFFICIENT["likely"],
             "note": "按本项目反对率与风险发生概率/激烈程度取值"}]


def judge_risk_level(total: float, qualitative: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """风险等级判定（参照件 第十二章 注 1）。

    Args:
        total: 稳评总分 W。
        qualitative: 直接定性指标命中情况，如
            {"群众反对率": 55, "群体性事件人数": 0, "恶性案件": False, "个人极端事件": False}

    Returns:
        {"level": "低风险", "basis": "…", "hit_qualitative": bool}
    """
    hits = []
    q = qualitative or {}
    for keys, level, label in QUALITATIVE_INDICATORS:
        field, op, val = keys
        v = q.get(field)
        if v is None or v == "" or v is False:
            continue
        # 布尔型指标（恶性案件/个人极端事件）：命中即为 True
        if op == "eq" and isinstance(val, bool):
            if bool(v) is True:
                hits.append((level, label))
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if op == "gt" and fv > val:
            hits.append((level, label))
        elif op == "ge" and fv >= val:
            hits.append((level, label))
        elif op == "between" and val[0] <= fv < val[1]:
            hits.append((level, label))
        elif op == "eq" and fv == val:
            hits.append((level, label))

    if hits:
        middles = [h for h in hits if h[0] == "中风险"]
        if len(hits) == 1 and middles:
            return {"level": "中风险", "hit_qualitative": True,
                    "basis": f"符合直接定性指标「{middles[0][1]}」，仅符合一项中风险情形，判定为中风险"}
        return {"level": "高风险", "hit_qualitative": True,
                "basis": "符合直接定性指标：" + "；".join(h[1] for h in hits) + "，判定为高风险"}

    if total >= THRESHOLD_HIGH:
        lv = "高风险"
    elif total > THRESHOLD_MID:
        lv = "中风险"
    else:
        lv = "低风险"
    return {"level": lv, "hit_qualitative": False,
            "basis": f"不符合直接定性指标任一情形；稳评总分 W={total}，按 "
                     f"W≥70 高风险 / 40<W<70 中风险 / W≤40 低风险 判定为{lv}"}


# ═══════════════════════════════════════════════════════════════
# Service
# ═══════════════════════════════════════════════════════════════

class ScoringService:

    def _pct(self, v: Any, default: float = 0.0) -> float:
        try:
            return float(str(v).replace('%', '').replace('％', '').strip())
        except (TypeError, ValueError):
            return default

    def _oppose_rate(self, facts: Dict) -> float:
        """本单位/本项目的**反对率**（百分比）。

        🔴 优先取显式的 `oppose_rate`；只有拿不到时才退回 `100 − 支持率`。
        退回算法会把「有条件支持 / 无所谓 / 未表态」一并算作反对 ——
        49 支持 + 1 未表态的 50 份问卷，会被算成 2% 反对，凭空虚增扣分。
        """
        v = str((facts or {}).get("oppose_rate") or "").strip()
        if v:
            try:
                return max(0.0, float(v.replace('%', '').replace('％', '').strip()))
            except (TypeError, ValueError):
                pass
        return max(0.0, 100.0 - self._pct((facts or {}).get("support_rate"), 100.0))

    def _build_pre_measures_table(self, facts: Dict, survey: Dict) -> List[ScoringItem]:
        """构建措施前评分表。

        🔴 只给**评分标准原文**与**尽量可自动计算的得分**；扣分依据文字留空，
        由章节 agent 依本项目事实卡撰写（`basis_hint` 告诉它该写什么）。
        """
        facts = facts or {}
        support = self._pct(facts.get("support_rate"), 100.0)
        oppose = self._oppose_rate(facts)

        items: List[ScoringItem] = []
        for cat, weight, name, max_score, criteria, hint in SCORING_SKELETON:
            it = ScoringItem(cat, weight, name, max_score, criteria, basis_hint=hint)

            # ── 可自动算的项 ──
            if name == "群众意见分析":
                po = compute_public_opinion(rows_from_oppose_rate(oppose))
                it.score = int(round(po["total"]))
                it.is_auto = True
            elif name == "群众满意度测评是否达标":
                it.score = 0 if support >= 85 else (4 if support >= 70 else 6)
                it.is_auto = True
            elif name in SENSITIVE_ITEMS:
                it.score = 0
                it.basis_hint = (hint or "") + "；敏感项——无确凿证据时不扣分，注明待专家评审"

            items.append(it)
        return items

    def compute_measures_after(self, pre_items: List[ScoringItem]) -> List[ScoringItem]:
        """措施后评分：按已落实的防范化解措施逐项下调扣分。

        🔴 具体下调多少由章节 agent 依第13章措施判定；这里只做「不高于措施前」的兜底，
        并把下调说明留给 agent 撰写。
        """
        post_items = []
        for item in pre_items:
            post = ScoringItem(item.category, item.weight, item.item,
                               item.max_score, item.criteria,
                               item.score, "", item.basis_hint, item.is_auto)
            post_items.append(post)
        return post_items

    def build_scoring_report(self, facts: Dict, survey: Dict = None,
                             qualitative: Dict = None) -> Dict:
        """构建评分报告（措施前 / 措施后 / 等级判定）。"""
        pre = self._build_pre_measures_table(facts or {}, survey or {})
        post = self.compute_measures_after(pre)
        pre_total = sum(i.score or 0 for i in pre)
        post_total = sum(i.score or 0 for i in post)
        judged = judge_risk_level(pre_total, qualitative)
        auto_count = sum(1 for i in pre if i.is_auto)

        return {
            "pre_measures": {
                "items": [{"category": i.category, "weight": i.weight, "item": i.item,
                           "max_score": i.max_score, "criteria": i.criteria,
                           "score": i.score, "basis": i.basis,
                           "basis_hint": i.basis_hint, "is_auto": i.is_auto} for i in pre],
                "total": pre_total,
                "weight_sum": sum(i.weight for i in pre[:1]) if False else 100,
            },
            "post_measures": {
                "items": [{"category": i.category, "weight": i.weight, "item": i.item,
                           "max_score": i.max_score, "criteria": i.criteria,
                           "score": i.score, "basis": i.basis} for i in post],
                "total": post_total,
            },
            "public_opinion": compute_public_opinion(
                rows_from_oppose_rate(self._oppose_rate(facts))),
            "meta": {
                "risk_level": judged["level"],
                "risk_level_basis": judged["basis"],
                "hit_qualitative": judged["hit_qualitative"],
                "auto_scored_items": auto_count,
                "manual_review_items": len(pre) - auto_count,
                "standard": "DB32/T4013-2021 / 参照件《白马湖…报告》第十二章",
                "thresholds": {"high": THRESHOLD_HIGH, "mid": THRESHOLD_MID},
            },
        }

    def format_for_llm(self, report: Dict) -> str:
        """把评分骨架交给章节 agent：**算好的给它用，没算的请它依事实判定并写依据**。"""
        pre = report["pre_measures"]
        meta = report["meta"]
        po = report.get("public_opinion") or {}

        lines = [
            "【量化评分 —— 权重/评分标准/已算出的分数**不得修改**；扣分依据文字由你依事实卡撰写】",
            f"措施前扣分合计: {pre['total']}分 | 风险等级: {meta['risk_level']}",
            f"  判定依据: {meta['risk_level_basis']}",
            f"  系统自动评分: {meta['auto_scored_items']}项 | 待你判定: {meta['manual_review_items']}项",
            "",
            "## 措施前评分明细（得分列 = 扣分值；0 = 不扣分，越高风险越大）",
        ]
        cur, cat_total = "", 0
        for it in pre["items"]:
            if it["category"] != cur:
                if cur:
                    lines.append(f"  [{cur}] 小计扣分: {cat_total}分")
                cur, cat_total = it["category"], 0
            cat_total += it["score"] or 0
            if it["score"] is None:
                lines.append(f"  · {it['item']}（最高扣{it['max_score']}分）→ **请你判定得分**")
                lines.append(f"      评分标准: {it['criteria']}")
                lines.append(f"      依据要写: {it.get('basis_hint','')}")
            else:
                lines.append(f"  · {it['item']}: 扣{it['score']}分 / 最高扣{it['max_score']}分"
                             + ("（系统按公式算出）" if it["is_auto"] else ""))
        lines.append(f"  [{cur}] 小计扣分: {cat_total}分")

        if po.get("rows"):
            lines.append("")
            lines.append("## 表12-3 群众意见分析（按公式计算，公式：每行 a×b×35）")
            lines.append("  序号 | 反对率百分比a | 风险发生概率或激烈程度b | 得分")
            for i, r in enumerate(po["rows"], 1):
                lines.append(f"  {i} | {r['a']*100:.2f}% | {r['b']} | {r['score']}")
            lines.append(f"  合计得分: {po['total']}分（=本项目「群众意见分析」项得分）")
            lines.append("  🔴 b 的取值：肯定/较大可能/强烈反对=1.0；有可能/较强烈=0.7；概率很小/一般=0.3。"
                         "如本项目应分群体列示，请按实际分档改写并保持合计一致。")

        lines.append("")
        lines.append("## 风险等级判定规则（表12-1 直接定性指标 + 总分区间）")
        lines.append("  ① 直接定性：超50%群众反对 / 可能引发200人以上群体性事件 / 恶性案件 / 个人极端事件 → 高风险；")
        lines.append("              20%–50%反对 / 可能引发100人以上群体性事件 → 中风险（仅符合一项中风险判中风险，其余判高风险）")
        lines.append("  ② 无一情形命中时按总分 W：W≥70 高风险；40<W<70 中风险；W≤40 低风险")
        lines.append(f"  本项目判定: {meta['risk_level']}（{meta['risk_level_basis']}）")

        post = report["post_measures"]
        lines.append("")
        lines.append(f"措施后扣分合计: {post['total']}分（应低于措施前 {pre['total']} 分）")
        lines.append("🔴 措施后各项的具体下调幅度，请依第13章措施逐项判定并写明依据。")
        return "\n".join(lines)


scoring_service = ScoringService()


# ── Compatible interface for report_assembler.py ──

@dataclass
class ScoreItem:
    indicator: str
    score: int
    max_score: int = 0
    is_auto: bool = False
    basis: str = ""


class ScoringCalculator:
    """Compatibility wrapper matching report_assembler.py's expected API."""

    def calculate(self, filled: dict) -> list:
        """Calculate pre-measures scores. Returns list of ScoreItem."""
        report = scoring_service.build_scoring_report(filled, {})
        items = []
        for i in report["pre_measures"]["items"]:
            items.append(ScoreItem(
                indicator=i["item"],
                score=i["score"] or 0,
                max_score=i["max_score"],
                is_auto=i["is_auto"],
                basis=i["basis"] or i.get("basis_hint", ""),
            ))
        return items

    def total(self, filled: dict) -> int:
        return scoring_service.build_scoring_report(filled, {})["pre_measures"]["total"]

    def calculate_measures_after(self, pre_items: list) -> list:
        """措施后评分清单（与措施前同结构）。

        🔴 新口径：措施后各项得分由**章节 agent** 依第 13 章措施逐项判定并写入正文，
        这里只回同样结构供组装器填表占位，**不再做「一刀切减 1 分」**那种粗糙处理。
        """
        out = []
        for i in list(pre_items or []):
            out.append(ScoreItem(
                indicator=getattr(i, "indicator", ""),
                score=getattr(i, "score", 0) or 0,
                max_score=getattr(i, "max_score", 0),
                is_auto=getattr(i, "is_auto", False),
                basis=getattr(i, "basis", ""),
            ))
        return out


scoring_calculator = ScoringCalculator()
