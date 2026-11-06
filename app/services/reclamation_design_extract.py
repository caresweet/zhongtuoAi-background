# -*- coding: utf-8 -*-
"""复垦**项目设计参数**的材料抽取（确定性正则，先查资料再问用户）。

业主口径（2026-11-03）：
    「项目设计参数 先从用户提供的资料中查找 然后再去问用户。」

所以弹窗之前先跑这里：从 `_project_materials[].text_content`（可研/初设/
复垦方案/施工组织设计的文本层）里找下列参数，找到就写进 `filled_data`，
弹窗（`RECLAMATION_NEED_FIELDS`）看到有值就不再问：

    topsoil_thickness     表土剥离/覆土厚度（存厘米，如 "30"）
    hard_surface_type     硬化结构做法（碎石垫层+钢板 / 混凝土面层+垫层）
    hard_area_m2          硬化/压占面积（平方米）
    surface_thickness_cm  结构层厚度（厘米）
    cushion_thickness_cm  垫层厚度（厘米，混凝土做法）
    service_years         土地复垦方案服务年限（“36 个月”）
    project_area_m2       项目区面积（平方米，含主体工程用地）
    project_investment    项目总投资（万元）
    construction_nature   项目性质（新建/改扩建…）

只填**空值**，绝不覆盖用户已填/已确认的值；单位统一换算成引擎口径。
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Tuple

logger = logging.getLogger(__name__)

_NUM = r'(\d+(?:\.\d+)?)'
_AREA_UNITS = r'(公顷|hm2|hm²|ha|平方米|㎡|m2|m²|亩)'
_LEN_UNITS = r'(cm|厘米|mm|毫米|m|米)'


def _area_m2(val: str, unit: str) -> float:
    v = float(val)
    u = unit.lower()
    if u in ("公顷", "hm2", "hm²", "ha"):
        return v * 10000.0
    if u == "亩":
        return v * 666.6667
    return v


def _cm(val: str, unit: str) -> float:
    v = float(val)
    u = unit.lower()
    if u in ("m", "米"):
        return v * 100.0
    if u in ("mm", "毫米"):
        return v / 10.0
    return v


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _fmt(v: float, nd: int = 2) -> str:
    if nd <= 0:
        return str(int(round(v)))
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


# ── 各字段的取数正则（按优先级排列，取第一个命中）────────────────────────
_TOP_PATTERNS = (
    re.compile(r'(?:表土剥离|剥离表土|剥离|表土覆盖|覆土)[^。；，]{0,16}?'
               r'(?:厚度|厚)?[^。；，]{0,8}?' + _NUM + r'\s*' + _LEN_UNITS),
    re.compile(r'(?:剥离|覆土)[^。；，]{0,10}?' + _NUM + r'\s*' + _LEN_UNITS),
)

_SURFACE_PREFIX = re.compile(
    _NUM + r'\s*' + _LEN_UNITS + r'\s*(?:厚)?(?:的)?\s*'
    r'(?:碎石|碎石子|钢板|混凝土面层|砼面层)')
_SURFACE_SUFFIX = re.compile(
    r'(?:碎石垫层|碎石层|钢板|混凝土面层|砼面层)[^。；，]{0,6}?(?:厚度|厚)?'
    r'[^。；，]{0,4}?' + _NUM + r'\s*' + _LEN_UNITS)
_CUSHION_SUFFIX = re.compile(
    r'(?:垫层)[^。；，]{0,6}?(?:厚度|厚)?[^。；，]{0,4}?' + _NUM + r'\s*' + _LEN_UNITS)
_CUSHION_PREFIX = re.compile(
    _NUM + r'\s*' + _LEN_UNITS + r'\s*(?:厚)?(?:的)?\s*(?:垫层)')

_HARD_AREA_PATTERNS = (
    re.compile(r'(?:硬化|压占|铺设钢板|铺钢板)[^。；，]{0,8}?(?:面积|范围)'
               r'[^。；，]{0,4}?' + _NUM + r'\s*' + _AREA_UNITS),
    # 施工组织设计常写「需清除地面垫层的面积为 2.0048hm2」
    re.compile(r'(?:清除地面垫层|地面垫层|场地铺筑|铺筑)[^。；，]{0,8}?面积'
               r'[^。；，]{0,4}?' + _NUM + r'\s*' + _AREA_UNITS),
)

_SERVICE = re.compile(
    r'(?:土地复垦方案服务年限|复垦方案服务年限|服务年限)[^。；，\d]{0,8}?'
    + _NUM + r'\s*(个月|月|年)')

# 服务年限的构成：人工稿写法「使用结束后6个月内完成…之后考虑6个月管护期」
_RECLAIM_PERIOD = re.compile(
    r'(?:使用结束后|期满后|使用期满后)[^。；，\d]{0,10}?' + _NUM + r'\s*个月内完成')
_CARE_PERIOD = re.compile(
    r'(?:之后|随后|再)[^。；，\d]{0,6}?' + _NUM + r'\s*个月的?管护')

_PROJECT_AREA = re.compile(
    r'(?:项目区面积|项目总面积|主体工程用地面积)[^。；，\d]{0,6}?'
    + _NUM + r'\s*' + _AREA_UNITS)

_INVESTMENT_PATTERNS = (
    # 「项目总投资 651003.92 万元」优先；排除复垦方案自己的静态/动态/复垦投资
    re.compile(r'(?<!静态)(?<!动态)(?:项目)?总投资(?:额|规模)?[^。；，\d]{0,6}?'
               + _NUM + r'\s*(万元|亿元)'),
    re.compile(r'(?<!静态)(?<!动态)(?<!复垦)(?:工程|项目)投资(?:额|规模)?'
               r'[^。；，\d]{0,6}?' + _NUM + r'\s*(万元|亿元)'),
)

_NATURE = re.compile(r'(?:项目性质|建设性质)\s*[：:\s]\s*(新建|改扩建|改建|扩建|技术改造)')


def _first(patterns: Iterable[re.Pattern], text: str):
    for p in patterns:
        m = p.search(text)
        if m:
            return m
    return None


def _surface_type(text: str) -> str:
    """结构做法：先看有没有碎石/钢板，再看混凝土。"""
    if re.search(r'(碎石垫层|碎石子|铺设钢板|铺钢板|钢板)', text):
        return "碎石垫层+钢板"
    if re.search(r'(混凝土面层|混凝土路面|砼面层)', text):
        return "混凝土面层+垫层"
    return ""


def extract_design_params(filled: dict, materials: Iterable[Tuple[str, str]]) -> List[str]:
    """从材料文本补设计参数。返回本次填上的键名列表。"""
    if not isinstance(filled, dict):
        return []
    texts: List[str] = []
    for m in (materials or []):
        if isinstance(m, dict):
            t = m.get("text_content")
        else:
            try:
                _n, t = m
            except (TypeError, ValueError):
                continue
        if t:
            texts.append(str(t))
    if not texts:
        return []
    blob = "\n".join(texts)
    filled_keys: List[str] = []

    def _put(key: str, value: str) -> None:
        if not value:
            return
        cur = str(filled.get(key) or "").strip()
        if cur and not cur.startswith(("【待", "待补充")):
            return
        filled[key] = value
        filled_keys.append(key)

    def _put_first(key: str, patterns, conv) -> None:
        m = _first(patterns, blob)
        if not m:
            return
        try:
            _put(key, conv(m.group(1), m.group(2)))
        except (TypeError, ValueError):
            return

    # ① 剥离/覆土厚度
    _put_first("topsoil_thickness", _TOP_PATTERNS,
               lambda v, u: _fmt(_cm(v, u), 0))

    # ② 结构做法 + 结构层厚度 + 垫层厚度
    _stype = _surface_type(blob)
    _put("hard_surface_type", _stype)
    if _stype:
        m = None
        # 先认「15cm 碎石垫层 / 15cm 混凝土面层」，再认「碎石垫层厚度 15cm」
        for _p in (_SURFACE_PREFIX, _SURFACE_SUFFIX):
            m = _p.search(blob)
            if m:
                break
        if m:
            # 混凝土时前缀可能先命中「10cm 垫层」——那是垫层厚度，不是面层
            _hit = m.group(0)
            if "混凝土" in _stype and "垫层" in _hit and "混凝土" not in _hit:
                m = None
        if m:
            _put("surface_thickness_cm", _fmt(_cm(m.group(1), m.group(2)), 0))
        m2 = _first((_CUSHION_SUFFIX, _CUSHION_PREFIX), blob)
        if m2:
            _put("cushion_thickness_cm", _fmt(_cm(m2.group(1), m2.group(2)), 0))

    # ③ 硬化/压占面积
    _put_first("hard_area_m2", _HARD_AREA_PATTERNS,
               lambda v, u: _fmt(_area_m2(v, u), 2))

    # ④ 服务年限
    def _svc(v, u):
        n = float(v) * (12 if u in ("年",) else 1)
        return f"{_fmt(n, 0)} 个月"

    _put_first("service_years", (_SERVICE,), _svc)

    # 复垦施工期 / 管护期（服务年限的构成）
    m = _RECLAIM_PERIOD.search(blob)
    if m:
        _put("reclaim_period_months", _fmt(float(m.group(1)), 0))
    m = _CARE_PERIOD.search(blob)
    if m:
        _put("care_period_months", _fmt(float(m.group(1)), 0))
    # 材料没直接写服务年限，但写了「占用期 + 复垦期 + 管护期」→ 相加
    if not str(filled.get("service_years") or "").strip():
        _lu = _num(filled.get("land_use_months"))
        _rp = _num(filled.get("reclaim_period_months"))
        _cp = _num(filled.get("care_period_months"))
        if _lu > 0 and _rp > 0 and _cp > 0:
            _put("service_years", f"{_fmt(_lu + _rp + _cp, 0)} 个月")

    # ⑤ 项目区面积 / 总投资 / 项目性质
    _put_first("project_area_m2", (_PROJECT_AREA,),
               lambda v, u: _fmt(_area_m2(v, u), 2))

    def _invest(v, u):
        n = float(v) * (10000 if u == "亿元" else 1)
        return _fmt(n, 2)

    _put_first("project_investment", _INVESTMENT_PATTERNS, _invest)
    m = _NATURE.search(blob)
    if m:
        _put("construction_nature", m.group(1))

    if filled_keys:
        logger.info(f"[复垦设计参数] 材料里识别到：{filled_keys}")
    return filled_keys
