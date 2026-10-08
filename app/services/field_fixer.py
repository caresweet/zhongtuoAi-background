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
