# -*- coding: utf-8 -*-
"""复垦正文收口：口径统一 + 串项目清理 + 单位规范。

解决专家评审（2026-11-09《测试_博里花园_复垦方案 评审之后》）指出的三类问题：

1. **串项目**：4.1.1 出现了「张兴矿区 / 采输卤 / ZX9、ZX10 井」等范文里别的项目内容
   —— 范文只教写法，不得把别的项目事实写进来；
2. **面积/土方口径打架**：同一份报告里出现 2.28 ha（耕地）、2.59 ha（剥离面积）、
   6840 m³、7770 m³、9780 m³ 三套数 —— 剥离面积必须统一取「耕地+园地+林地+草地」，
   土方量 = 剥离面积 × 厚度；
3. **单位/图注**：`hm` 缺上标、`m` 缺立方；「由图可知」但本节没有图。

本模块只对 **复垦** 生效（稳评在 `_clean_content` 那条链路上不受影响）。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_STRIP_CLASSES = ("耕地", "林地", "园地", "草地")

# 范文/样板的项目名特征词。命中且**当前项目名里没有** → 判为串项目。
_FOREIGN_MARKERS = (
    "井神", "张兴矿区", "张兴储气库", "采输卤", "明远", "王元佳苑", "宁淮",
    "通甫路", "黄码", "食品科技产业园",
)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _class_name(full) -> str:
    return re.sub(r'[（(].*?[)）]', '', str(full or "")).strip()


def _rows(filled: dict) -> List[dict]:
    lc = (filled or {}).get("land_classification") or {}
    return [r for r in (lc.get("rows") or []) if isinstance(r, dict)]


def _thickness_m(filled: dict) -> float:
    v = _num((filled or {}).get("topsoil_thickness"))
    if v <= 0:
        return 0.0
    return v / 100.0 if v > 1.0 else v


def topsoil_facts(filled: dict) -> Optional[Dict[str, object]]:
    """本项目表土口径（剥离面积/厚度/土方量/复垦方向）。

    与 `reclamation_ch4_tables.table_4_11`、`reclamation_quantities` 同一算法，
    不另立口径。取不到地类或厚度返回 None。
    """
    rows = _rows(filled)
    if not rows:
        return None
    strip_m2 = sum(_num(r.get("面积_m2")) for r in rows
                   if _class_name(r.get("一级地类")) in _STRIP_CLASSES)
    thick = _thickness_m(filled)
    if strip_m2 <= 0 or thick <= 0:
        return None
    total_m2 = _num(((filled or {}).get("land_classification") or {}).get("合计_m2")) \
        or sum(_num(r.get("面积_m2")) for r in rows) \
        or _num((filled or {}).get("area_m2"))
    farm_m2 = sum(_num(r.get("面积_m2")) for r in rows
                  if _class_name(r.get("一级地类")) == "耕地")
    vol = strip_m2 * thick

    # 复垦方向：优先用分区表里用户填的方向（去重保序），否则取耕地二级类
    dirs: List[str] = []
    try:
        from app.services.reclamation_zones import zones_from_filled
        for z in zones_from_filled(filled) or []:
            d = str((z or {}).get("reclaim_direction") or "").strip()
            if d and d not in dirs:
                dirs.append(d)
    except Exception:
        pass
    if not dirs:
        for r in rows:
            if _class_name(r.get("一级地类")) == "耕地":
                d = _class_name(r.get("二级地类"))
                if d and d not in dirs:
                    dirs.append(d)
    return {
        "strip_m2": strip_m2, "strip_ha": strip_m2 / 10000.0,
        "total_m2": total_m2, "total_ha": total_m2 / 10000.0,
        "farm_m2": farm_m2, "farm_ha": farm_m2 / 10000.0,
        "thickness_m": thick, "volume_m3": vol,
        "directions": dirs,
    }


def facts_block(filled: dict) -> str:
    """给章节提示词的「已核定口径」块；无数据返回空串。"""
    f = topsoil_facts(filled)
    if not f:
        return ""
    _dirs = "、".join(f["directions"] or ["耕地"])
    _diff = ""
    if abs(f["farm_m2"] - f["strip_m2"]) > 1:
        _diff = (f"\n- 注意：耕地面积是 {f['farm_ha']:.4f} 公顷，**不是**剥离面积；"
                 f"写剥离/覆土/土方时只能用 {f['strip_ha']:.4f} 公顷。")
    return (
        "\n\n## 🔒 系统已核定的表土口径（全章数字必须与此一致）\n"
        f"- 临时用地面积：{f['total_ha']:.4f} 公顷（{f['total_m2']:.0f} ㎡）\n"
        f"- 剥离表土面积：{f['strip_ha']:.4f} 公顷（{f['strip_m2']:.0f} ㎡，"
        f"耕地+园地+林地+草地）\n"
        f"- 剥离/覆土厚度：{f['thickness_m']:.2f} m\n"
        f"- 可供土方量 = 需土方量：{f['volume_m3']:.1f} m³（剥覆平衡）\n"
        f"- 表土剥离工程量：{f['volume_m3'] / 100.0:.2f} 100m³；"
        f"表土回填工程量：{f['volume_m3'] / 100.0:.2f} 100m³\n"
        f"- 复垦方向：{_dirs}"
        + _diff + "\n"
        "正文、表格、工程量与投资估算引用上述数字时必须一字不差；"
        "不得用「耕地面积」代替「剥离面积」，也不得另行推算其他土方量。\n"
    )


def _strip_foreign_projects(text: str, filled: dict) -> str:
    """删掉写着**别的项目**的段落（范文事实串进本项目）。"""
    proj = f"{filled.get('project_name') or ''}{filled.get('location') or ''}"
    bad = [m for m in _FOREIGN_MARKERS if m not in proj]
    if not bad:
        return text
    out, dropped = [], 0
    for line in str(text or "").split("\n"):
        s = line.strip()
        # 标题、表格、图片行不删（标题本身不含别的项目名）
        if s.startswith("#") or s.startswith("|") or s.startswith("!["):
            out.append(line)
            continue
        if any(m in line for m in bad):
            dropped += 1
            continue
        out.append(line)
    if dropped:
        logger.info(f"[复垦收口] 删除串入其他项目的段落 {dropped} 段（{bad[:3]}…）")
    return "\n".join(out)


def _fix_numbers(text: str, filled: dict) -> str:
    """把用「耕地面积」顶替「剥离面积」的数字改正（只改带明确标签的地方）。"""
    f = topsoil_facts(filled)
    if not f or abs(f["farm_m2"] - f["strip_m2"]) <= 1:
        return text
    bad_vol = f["farm_m2"] * f["thickness_m"]

    def _sub_number(seg: str, label_pat: str, bad: str, good: str) -> str:
        return re.sub(
            r'(' + label_pat + r'[^0-9]{0,8})' + re.escape(bad) + r'(?![0-9.])',
            lambda m: m.group(1) + good, seg)

    def _force_after(seg: str, label_pat: str, good: str) -> str:
        """标签后面不管写的是几，统一改成系统口径（模型会自造 6812.6 这种数）。"""
        return re.sub(
            r'(' + label_pat + r'[^0-9]{0,10})\d+(?:\.\d+)?',
            lambda m: m.group(1) + good, seg)

    _VOL_LABEL = (r'(?:可供表土(?:量)?|表土可供(?:方量|土方|量)?|可供土方量|可供土方|'
                  r'覆土(?:实际)?可用|需土量|需土方量|需剥离表土量|需覆盖表土(?:量)?|'
                  r'表土回填量|回填量|共需表土|共计剥离表土|所需土方量|'
                  r'表土回覆?所需土方(?:量)?|回覆?所需土方(?:量)?)')

    out = []
    for line in str(text or "").split("\n"):
        if line.strip().startswith("|"):          # 表格行：系统表已算好，不动
            out.append(line)
            continue
        parts = re.split(r'(?<=[。；;])', line)
        fixed = []
        # 剥离/覆土面积的标签（模型写法很多：需剥离表土面积、需回填表土面积、
        # 覆土面积…，都要能把「耕地面积」的数纠回来）
        _ha_label = (r'(?:剥离(?:表土)?面积|(?:实际)?需剥离表土面积|需回填表土面积|'
                     r'回填表土面积|覆土面积|表土剥离量按(?:耕地)?面积|'
                     r'按(?:耕地)?面积)(?:为|是|约|共)?')
        _strip_ctx = False
        for seg in parts:
            if "剥离范围" in seg:
                _strip_ctx = True
            # 剥离面积：公顷 / 平方米两种写法
            for bad, good in ((f"{f['farm_ha']:.4f}", f"{f['strip_ha']:.4f}"),
                              (f"{f['farm_ha']:.2f}", f"{f['strip_ha']:.2f}"),
                              (f"{f['farm_m2']:.0f}", f"{f['strip_m2']:.0f}")):
                seg = _sub_number(seg, _ha_label, bad, good)
            # 「剥离范围…。合计22800平方米。」—— 合计跟着剥离范围走
            if _strip_ctx and "合计" in seg:
                seg = _force_after(seg, r'合计', f"{f['strip_m2']:.0f}")
            # 土方量：不论模型写的是什么数，一律归到系统口径
            seg = _force_after(seg, _VOL_LABEL, f"{f['volume_m3']:.0f}")
            # 「表土剥离量按…计算，为 6840 m³」—— 剥离量句里的「为+数字」也归口径
            #    （只认 3–5 位整数，别把「厚度为 0.30」也改掉）
            if "剥离量" in seg and ("表土" in seg or "剥离" in seg):
                seg = re.sub(
                    r'((?:为|即|共)\s*)\d{3,5}(?:\.\d+)?',
                    lambda m: m.group(1) + f"{f['volume_m3']:.0f}", seg)
            fixed.append(seg)
        out.append("".join(fixed))
    return "\n".join(out)


def _fix_units(text: str) -> str:
    """hm / hm2 → hm²；表土/土方句里的裸 m → m³。"""
    text = re.sub(r'hm\s?2|hm(?![a-zA-Z²])', 'hm²', text)
    out = []
    for line in str(text or "").split("\n"):
        if line.strip().startswith("|"):
            out.append(line)
            continue
        parts = re.split(r'(?<=[。；;])', line)
        fixed = []
        for seg in parts:
            if any(k in seg for k in ("土方", "表土", "回填", "覆土",
                                       "垫层", "清理", "外运")):
                seg = re.sub(r'(\d{3,7}(?:\.\d+)?)\s*m(?=[，。；、\s]|$)',
                             r'\1 m³', seg)
            fixed.append(seg)
        out.append("".join(fixed))
    return "\n".join(out)


def _fix_figure_reference(text: str, chapter_no: int) -> str:
    """「由图可知」但该节没有图 → 改成依据表述（仅复垦第4章）。"""
    if int(chapter_no) != 4:
        return text
    return str(text or "").replace(
        "由图可知，", "根据现场调查及自然资源主管部门三区三线成果，").replace(
        "由图可知", "根据现场调查及自然资源主管部门三区三线成果")


def apply_guards(content: str, filled: dict, chapter_no: int,
                 domain: str) -> str:
    """复垦正文收口入口；其他域原样返回。"""
    if str(domain) != "reclamation" or not content:
        return content
    try:
        content = _strip_foreign_projects(content, filled or {})
        content = _fix_numbers(content, filled or {})
        content = _fix_units(content)
        content = _fix_figure_reference(content, chapter_no)
    except Exception as e:                       # 收口失败不影响出报告
        logger.warning(f"[复垦收口] 第{chapter_no}章处理失败: {e}")
    return content
