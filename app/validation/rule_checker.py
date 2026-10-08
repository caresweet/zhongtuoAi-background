# -*- coding: utf-8 -*-
"""校验兜底：阈值规则库 + 异常值识别 + 必填核对单。

- 规则库：`data/check_rules.json`（业务可在界面 `/api/v1/check-rules` 修改，动态生效）
- 在生成前/生成后对 `filled_data` 的可量化指标跑规则，产出 预警/异常 问题清单
- 必填核对单：报告核心字段缺失提示（对齐 11 章案卷结构）

规则类型（`rule` 字段）：
    ge / le / eq / between    —— 阈值校验
    type="outlier"            —— 异常值识别（偏离历史均值 / 常模区间）

🔴 与旧版的关键差异：**指标缺失不再静默通过**。
旧实现里 `val is None → continue`，导致「新规则没接上指标」时规则形同不存在；
现在显式产出 `metric_missing` 条目（`include_missing=True` 时返回）。
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
RULES_FILE = ROOT / "data" / "check_rules.json"

# 报告必填核对单（对齐 11 章正文结构；评审表为不编号前置部分）
REQUIRED_FIELDS = [
    {"field": "project_name", "label": "项目/决策名称", "chapter": "评审表"},
    {"field": "doc_reference", "label": "征地预公告文号", "chapter": "评审表"},
    {"field": "org_name", "label": "稳评责任单位", "chapter": "评审表"},
    {"field": "implement_unit", "label": "稳评实施单位", "chapter": "评审表"},
    {"field": "location", "label": "拟征收位置", "chapter": "第3章"},
    {"field": "area_m2", "label": "总面积(㎡)/勘测定界", "chapter": "第3章"},
    {"field": "land_use", "label": "土地用途", "chapter": "第3章"},
    {"field": "household_count", "label": "涉及户数", "chapter": "第3章"},
    {"field": "population_count", "label": "涉及人口", "chapter": "第3章"},
    {"field": "total_samples", "label": "问卷回收/有效份数", "chapter": "第5章"},
    {"field": "support_rate", "label": "群众支持率", "chapter": "第5章"},
    {"field": "compensation_standard", "label": "补偿标准", "chapter": "第8章"},
]


def load_rules() -> List[Dict[str, Any]]:
    if RULES_FILE.exists():
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_rules(rules: List[Dict[str, Any]]) -> None:
    RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.\-]", "", v.strip().replace("%", ""))
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract_metrics(filled: dict) -> Dict[str, float]:
    """从 filled_data 尽力提取可量化指标（metric_key -> float）。

    指标键与 `check_rules.json` 里的 `metric` 一一对应。
    """
    filled = filled or {}
    out: Dict[str, float] = {}

    def _first(*keys):
        for k in keys:
            v = filled.get(k)
            n = _to_number(v)
            if n is not None:
                return n
        return None

    # 支持率 / 反对率 / 知晓率
    r = _first("support_rate", "满意率", "支持率")
    if r is not None:
        out["群众支持率"] = r
    r = _first("oppose_rate", "反对率")
    if r is not None:
        out["反对率"] = r
    r = _first("awareness_rate", "知晓率")
    if r is not None:
        out["知晓率"] = r

    # 问卷份数（按此优先级，绝不用户数冒充）
    r = _first("total_samples", "survey_total_count", "valid_surveys", "total_surveys")
    if r is not None:
        out["有效问卷份数"] = r

    # 户数 / 人口
    r = _first("household_count")
    if r is not None:
        out["涉及户数"] = r
    r = _first("population_count")
    if r is not None:
        out["涉及人口"] = r
    # 户均人口
    if out.get("涉及户数") and out.get("涉及人口"):
        out["户均人口"] = round(out["涉及人口"] / out["涉及户数"], 2)

    # 面积
    r = _first("area_m2")
    if r is not None:
        out["征收面积㎡"] = r
    r = _first("area_mu")
    if r is not None:
        out["征收面积亩"] = r

    # 部门参与数
    r = _first("dept_survey_count", "dept_count")
    if r is not None:
        out["参与部门数"] = r

    # OCR 低置信字段占比
    conf = filled.get("_ocr_confidence") or {}
    if isinstance(conf, dict) and conf:
        low = sum(1 for v in conf.values()
                  if isinstance(v, dict) and isinstance(v.get("confidence"), (int, float))
                  and v["confidence"] < 0.85)
        out["OCR低置信占比"] = round(low / len(conf), 3)

    # 回收率 / 覆盖率 / 通用扁平键
    flat: Dict[str, Any] = {}

    def walk(d, pre=""):
        if isinstance(d, dict):
            for k, vv in d.items():
                walk(vv, pre + str(k))
        elif isinstance(d, list):
            for vv in d:
                walk(vv, pre)
        else:
            flat[pre] = d
    walk(filled)
    for k, v in flat.items():
        if not isinstance(v, (str, int, float)):
            continue
        if any(t in k for t in ("回收率", "有效回收")):
            n = _to_number(v)
            if n is not None:
                out.setdefault("问卷回收率", n)
        if "覆盖" in k or ("座谈" in k and "%" in str(v)):
            n = _to_number(v)
            if n is not None:
                out.setdefault("座谈/村组覆盖率", n)
    return out


def _pass(rule: Dict[str, Any], val: float, *, history: bool = True) -> Optional[bool]:
    """单条规则判定。返回 True=通过 / False=不通过 / None=未知规则（不拦截）。"""
    rtype = rule.get("rule")
    v = rule.get("value")

    if rule.get("type") == "outlier":
        if not history:
            return None
        from app.validation.history_stats import is_outlier
        params = rule.get("params") or {}
        flag, _info = is_outlier(
            _metric_key_of(rule), val,
            sigma=float(params.get("sigma", 2.0)),
            min_n=int(params.get("min_n", 5)),
            fallback=params.get("fallback") or (v if isinstance(v, list) else None),
        )
        return (not flag)

    if rtype == "ge":
        return val >= float(v) - 1e-9
    if rtype == "le":
        return val <= float(v) + 1e-9
    if rtype == "eq":
        return abs(val - float(v)) < 1e-9
    if rtype == "between" and isinstance(v, list) and len(v) == 2:
        return float(v[0]) <= val <= float(v[1])
    return None


def _metric_key_of(rule: Dict[str, Any]) -> str:
    """规则 → 历史统计用的指标键（优先 field_keys 里的英文键）。"""
    for k in (rule.get("field_keys") or []):
        if re.fullmatch(r'[a-z_]+', str(k)):
            return str(k)
    return str(rule.get("metric") or "")


def run_checks(filled: dict, *, history: bool = True,
               include_missing: bool = False,
               rules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """对 filled_data 跑启用规则，返回问题清单。

    Args:
        filled: 事实卡。
        history: 是否启用异常值（历史分布）判定。
        include_missing: 是否把「指标缺失」也作为条目返回
            （默认 False —— 缺失由 `missing_checklist` 统一提示）。
        rules: 规则列表；缺省读 `data/check_rules.json`。

    Returns:
        问题条目列表，每条含 rule_id / metric / actual / limit / severity / basis / message。
    """
    issues: List[Dict[str, Any]] = []
    metrics = extract_metrics(filled)
    for rule in (rules if rules is not None else load_rules()):
        if not rule.get("enabled"):
            continue
        # type="count" 的规则（页数/附件数）在组装阶段由 report_assembler 评估，
        # 这里不参与 filled_data 指标校验。
        if rule.get("type") == "count":
            continue
        label = rule.get("label") or rule.get("metric")

        val = metrics.get(rule.get("metric"))
        if val is None:
            for k in (rule.get("field_keys") or []):
                if k in metrics:
                    val = metrics[k]
                    break

        # 🔴 指标缺失：显式产出（旧版此处静默 continue，导致规则形同不存在）
        if val is None:
            if include_missing:
                issues.append({
                    "rule_id": rule.get("id"), "metric": label,
                    "reason": "metric_missing", "severity": "info",
                    "actual": None, "limit": rule.get("value"),
                    "basis": rule.get("basis", ""),
                    "message": f"{label} 未取到指标值，规则未能校验（请确认数据是否已提取）",
                })
            continue

        ok = _pass(rule, val, history=history)
        if ok is None:
            continue
        if not ok:
            unit = rule.get("unit", "")
            issues.append({
                "rule_id": rule["id"],
                "metric": label,
                "rule": rule.get("rule"),
                "limit": rule.get("value"),
                "unit": unit,
                "actual": val,
                "severity": rule.get("severity", "warn"),
                "basis": rule.get("basis", ""),
                "note": rule.get("note", ""),
                "message": f"{label} 实测 {val}{unit}，未满足要求（{rule.get('value')}{unit}）",
            })
    return issues


def missing_checklist(filled: dict) -> List[Dict[str, Any]]:
    """必填核对单：列出缺失核心字段（含 `【待补充】` 占位）。"""
    filled = filled or {}
    missing = []
    for f in REQUIRED_FIELDS:
        v = filled.get(f["field"])
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().startswith("【"))):
            missing.append({**f, "status": "missing"})
    return missing
