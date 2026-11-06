"""用「解析出的表格」纠正被正则抓错的字段。

设计原则（对应需求「抓取位置不要固定」）：
- **不锁位置**：不依赖「第几页第几行」，按**表头语义**找列（含「土地用途」的列…）
- **保证数据量**：能匹配到就用表里的真实值覆盖正则的错值
"""
import re


def _col_index(headers, *keywords):
    """按表头语义找列序号（忽略空格/括号）。"""
    for i, h in enumerate(headers or []):
        hs = re.sub(r'[\s()（）]', '', str(h))
        if any(k in hs for k in keywords):
            return i
    return None


def _first_data_row(rows, ci, skip=("合计", "总计", "小计")):
    """取该列第一条真实数据（跳过合计行）。"""
    for r in rows or []:
        if ci < len(r):
            v = str(r[ci]).strip().replace("\n", "")
            if v and not any(s in v for s in skip):
                return v
    return None


def fix_fields_from_tables(filled: dict, tables: list) -> dict:
    """按表头语义，从勘测定界表(表1-1)纠正 land_use / location；

    并：① 由文号派生 project_name（避免抓成「…征收土地预公告」）；
        ② 丢弃与表坐落冲突的 land_location（治「勾搭村七组」这类污染）。
    """
    if not isinstance(filled, dict):
        return filled
    for t in (tables or []):
        if not isinstance(t, dict):
            continue
        hdr = t.get("headers") or []
        rows = t.get("rows") or []
        if not hdr or not rows:
            continue
        hstr = " ".join(str(h) for h in hdr)
        # 只认「地块汇总表」（含 坐落/用途/地块号）
        if not any(k in hstr for k in ("坐落", "土地用途", "地块号", "界址")):
            continue

        ci = _col_index(hdr, "土地用途", "用途")
        if ci is not None:
            v = _first_data_row(rows, ci)
            if v and len(v) <= 24:
                filled["land_use"] = v
                filled["_locked_land_use"] = True

        ci = _col_index(hdr, "土地坐落", "坐落")
        if ci is not None:
            v = _first_data_row(rows, ci)
            if v:
                filled["location"] = v
                filled["_locked_location"] = True

    # 项目名称：直接用预公告标题（用户要求），仅在标题缺失时由文号派生
    from app.services.announcement_extract import clean_project_title, derive_project_name
    dr = str(filled.get("doc_reference", "")).strip()
    pn = str(filled.get("project_name", "")).strip()
    if pn and any(k in pn for k in ("公告", "预公告", "公示")):
        pn = clean_project_title(pn)
    if not pn and dr:
        pn = derive_project_name(dr)
    if pn:
        filled["project_name"] = pn
        filled["_locked_project_name"] = True

    # 位置冲突处理（原逻辑是「不一致就丢弃 land_location」）：
    # 🔴 现在改成**先归一、再判冲突** —— `land_location` 是预公告/扫描件 OCR 出来的
    #    位置，`location` 是报告字段名，两者同义。原逻辑在 `location` 为空时
    #    直接把 `land_location` 丢掉 = 材料里识别到的位置白白浪费，正文还得问用户。
    ll = str(filled.get("land_location", "")).strip()
    loc = str(filled.get("location", "")).strip()
    if ll and not loc:
        filled["location"] = ll
        filled["_locked_location"] = True
    elif ll and loc and ll != loc:
        filled.pop("land_location", None)

    return filled


# ═══════════════════════════════════════════════════════════════════════════════
# 复垦：从材料原文补 build_unit / 用地时间（业主 2026-10-31 实测缺陷）
# ═══════════════════════════════════════════════════════════════════════════════
# 材料里有「主体建设单位.docx」：首行是「江苏苏盐井神股份有限公司第一分公司」，
# 第二行是「用地时间 2026.10.1-------2028.9.30」。
# 但既有抽取器只认「标签：值」的冒号写法，两条都没进 filled_data ——
# 结果第1章把建设单位写成「建设单位」泛称、服务年限**编成 48 个月**（实际 24 个月）。
# 这里用确定性规则补上：用地时间区间 → 起止 + 月数；文件名带「建设单位」的文档首行 → build_unit。
import re as _re

import logging as _logging
_log = _logging.getLogger(__name__)

# 「项目名称 / 单位名称」不可信值的判据 —— 2026-11-02 实测两个脏值：
#   · project_name = 「自然资规〔2019〕1号土地征收决策」 ← 批复文号被当成了项目名
#   · project_unit = 「 /申请人）"」                   ← 句子残片被抓成了单位名
_BAD_NAME_CHARS_RE = _re.compile(r'[“”"\']')
_BAD_NAME_EDGE_RE = _re.compile(r'^[\s/、，,。；;：:（）()【】\[\]]|[（）)】\]\s]+$')
_DOC_NO_NAME_RE = _re.compile(r'^.{0,10}〔\d{4}〕\s*\d+\s*号')


def _valid_name(v) -> str:
    """名称类字段的清洗：不像「项目名/单位名」的一律返回空串。"""
    s = str(v or "").strip()
    if len(s) < 3 or len(s) > 80:
        return ""
    if _BAD_NAME_CHARS_RE.search(s) or _BAD_NAME_EDGE_RE.search(s):
        return ""
    if _DOC_NO_NAME_RE.match(s):
        return ""
    return s

_PERIOD_RE = _re.compile(
    r'(?:用地时间|使用期限|用地期限|临时用地期限|复垦期限)\s*[:：]?\s*'
    r'(\d{4})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]?\s*(\d{1,2})?\s*日?\s*'
    r'[-—~～至到]+\s*'
    r'(\d{4})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]?\s*(\d{1,2})?'
)


def _derive_hard_area_from_zones(filled: dict) -> None:
    """硬化面积兜底：材料没给时用「总面积 − 表土堆放区」（井神安井组口径）。"""
    if not isinstance(filled, dict) or str(filled.get("hard_area_m2") or "").strip():
        return
    try:
        from app.services.reclamation_zones import zones_from_filled, stockpile_area_m2
        _zones = zones_from_filled(filled)
        if not _zones:
            return
        _lc = filled.get("land_classification") or {}
        _total = float(str((_lc.get("合计_m2") if isinstance(_lc, dict) else 0)
                           or filled.get("area_m2") or 0).replace(",", "") or 0)
        _sp = stockpile_area_m2(_zones)
        if _total > 0 and 0 < _sp < _total:
            filled["hard_area_m2"] = f"{_total - _sp:.2f}"
            from app.services import field_sources as _fs
            _fs.mark(filled, _fs.USER, "hard_area_m2")
    except Exception as e:                       # pragma: no cover - 兜底不阻断
        _log.info(f"[复垦补全] 硬化面积兜底跳过: {e}")


def fix_reclamation_fields(filled: dict, materials: list, title: str = "") -> dict:
    """复垦：从 `_project_materials[].text_content` 补关键事实（幂等、不改已有值）。"""
    if not isinstance(filled, dict):
        return filled

    # ⓪ 名称类字段清洗（见 `_valid_name`）。必须在「没有材料就返回」之前做 ——
    #    脏值是抽取器写进来的，跟材料文本在不在无关。
    for _k in ("project_name", "project_unit", "build_unit"):
        _v = filled.get(_k)
        if not _v:
            continue
        _clean = _valid_name(_v)
        if not _clean:
            _log.warning(f"[复垦补全] 名称不可信，已清空 {_k}={_v!r}")
            filled.pop(_k, None)
            filled.pop(f"_locked_{_k}", None)
        elif _clean != str(_v).strip():
            filled[_k] = _clean
    # 项目名称兜底：报告标题去掉「土地复垦方案」后缀
    # （标题是用户/需求给的，比抽取器的猜测可靠）
    if not filled.get("project_name") and title:
        _t = _re.sub(r'[（(]?土地复垦方案.*$', '', str(title)).strip(" -—_")
        if len(_t) >= 4:
            filled["project_name"] = _t

    # ⓪a 功能分区（表1-1）：用户按甲方沟通结果填「名称+面积」→ 解析并落库。
    #     硬化面积**不在这里兜底**：材料里常写得比「总面积−表土堆放区」准
    #     （城东污水厂实测：材料 1.1000 公顷，按分区倒算是 2.38 公顷），
    #     放到材料抽取之后再补（见 ④a）。
    try:
        from app.services.reclamation_zones import zones_from_filled
        _zones = zones_from_filled(filled)
        if _zones:
            from app.services import field_sources as _fs
            _fs.mark(filled, _fs.USER, "land_use_zones")
    except Exception as _ze:
        _log.warning(f"[复垦补全] 功能分区解析跳过: {_ze}")

    items = []
    for m in (materials or []):
        if isinstance(m, dict):
            items.append((str(m.get("source_name") or ""), str(m.get("text_content") or "")))
            continue
        try:                                    # 兼容 (文件名, 正文) 二元组
            _name, _text = m
            items.append((str(_name or ""), str(_text or "")))
        except (TypeError, ValueError):
            continue
    if not items:
        _derive_hard_area_from_zones(filled)
        return filled

    # ① 用地时间 → 起止 + 月数
    if not filled.get("land_use_start"):
        for _name, _t in items:
            mm = _PERIOD_RE.search(_t)
            if not mm:
                continue
            y1, m1, d1, y2, m2, d2 = (mm.group(i) for i in range(1, 7))
            filled["land_use_start"] = f"{y1}年{int(m1)}月" + (f"{int(d1)}日" if d1 else "")
            filled["land_use_end"] = f"{y2}年{int(m2)}月" + (f"{int(d2)}日" if d2 else "")
            # 🔴 含头含尾算月数：2026.10.1→2028.9.30 实际是 24 个月，
            #    只做月份相减会算成 23（差一天到不了整月）。
            _d1, _d2 = int(d1 or 1), int(d2 or 1)
            months = (int(y2) - int(y1)) * 12 + (int(m2) - int(m1)) + (1 if _d2 >= _d1 else 0)
            if months > 0:
                filled["land_use_months"] = f"{months}个月"
            filled["land_use_period"] = f"{filled['land_use_start']}至{filled['land_use_end']}"
            break

    # ② 建设单位：文件名带「建设单位/项目单位/用地单位」的文本，首行就是单位名
    if not filled.get("build_unit"):
        for _name, _t in items:
            if not any(k in _name for k in ("建设单位", "项目单位", "用地单位", "复垦义务")):
                continue
            first = next((ln.strip() for ln in _t.splitlines() if ln.strip()), "")
            if first and len(first) <= 40 and ("公司" in first or "单位" in first or "局" in first):
                filled["build_unit"] = first
                break

    # ③ 报告表用的键名是 `project_unit`（项目概况「单位名称」），来源就是 build_unit。
    #    不打通的话，材料里明明识别出了用地单位，弹窗还会再问一次「单位名称」。
    if not filled.get("project_unit") and filled.get("build_unit"):
        filled["project_unit"] = filled["build_unit"]

    # ④ 项目设计参数：先从本项目材料里找（剥离厚度/硬化做法/面积/层厚/服务年限/
    #    项目区面积/总投资/项目性质），找到就不弹窗问（业主 2026-11-03）。
    try:
        from app.services.reclamation_design_extract import extract_design_params
        _design = extract_design_params(filled, items)
        if _design:
            from app.services import field_sources as _fs
            _fs.mark(filled, _fs.MATERIAL, *_design)
            _log.info(f"[复垦补全] 设计参数取自材料：{_design}")
    except Exception as _de:
        _log.warning(f"[复垦补全] 设计参数抽取跳过: {_de}")

    # ④a 材料没有硬化面积时才按分区倒算
    _derive_hard_area_from_zones(filled)

    try:
        from app.services import field_sources as _fs
        _fs.mark(filled, _fs.MATERIAL,
                 "land_use_start", "land_use_end", "land_use_months",
                 "land_use_period", "build_unit", "project_unit")
    except Exception:
        pass
    return filled
