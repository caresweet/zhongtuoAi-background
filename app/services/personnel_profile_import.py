# -*- coding: utf-8 -*-
"""从范文模板 / 人员统计表导入「公司资质 + 主要编制人员」到数据库。

🔴 业主 2026-11-03：「人员的资质信息你从模板或者我后期给你一个统计，
   计入数据库中查找。」—— 报告表里的发证机关/资质等级/联系人/电话、
   人员职称/专业，**不再每次弹窗问**，而是先查库；库里没有才问。

数据来源两条：
  1. **范文模板**（复垦 docx/PDF 的报告表页）——已经有的就提取；
  2. **人员统计表**（xlsx/csv，列：姓名 / 职称/职务 / 专业）——业主后期提供。

入库规则：**只补空，不覆盖**已经人工维护过的值；人员按 `(姓名, 公司)` 唯一。
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 报告表里标签 → 内部字段
_LABEL_MAP = {
    "编制单位名称": "name",
    "法人代表": "leader_name",
    "资质证书名称": "qualification_text",
    "资质等级": "qualification_level",
    "发证机关": "qualification_authority",
    "编号": "qualification_no",
    "联系人": "contact",
    "联系电话": "phone",
}


def _cell(v: Any) -> str:
    return re.sub(r"\s+", "", str(v or "")).strip()


def _val_after(row: Sequence[Any], label: str) -> str:
    """取标签右侧第一个非空单元格（合并单元格在 pdfplumber 里会重复/为空）。"""
    cells = list(row or [])
    for i, c in enumerate(cells):
        if label in _cell(c):
            for nxt in cells[i + 1:]:
                if _cell(nxt):
                    return _cell(nxt)
            return ""
    return ""


def _extract_from_rows(rows: Iterable[Sequence[Any]]) -> Dict[str, Any]:
    """从「标签｜值｜标签｜值」行里抽报告表字段 + 人员行。"""
    out: Dict[str, Any] = {"personnel": []}
    rows = list(rows or [])
    in_staff = False
    # 🔴 报告表上半部分是**用地单位**的信息（负责人/联系电话），
    #    下半部分才是**方案编制单位**（本公司）。不切开就会把用地单位的
    #    联系电话当成编制单位电话（实测：取到 17766118889，应为 13770380120）。
    in_company = False
    for row in rows:
        cells = [_cell(c) for c in (row or [])]
        if not any(cells):
            continue
        if "编制单位名称" in cells:
            in_company = True
        if "姓名" in cells and any("职称" in c for c in cells):
            in_staff = True
            continue
        if in_staff:
            # [.., 姓名, 职称/职务, 专业, 单位, 签名]
            nonempty = [c for c in cells if c]
            if len(nonempty) >= 2 and not any(k in "".join(cells) for k in ("合计", "备注")):
                name_i = cells.index("姓名") if "姓名" in cells else 1
                name = cells[name_i] if name_i < len(cells) else ""
                if not name:
                    # 姓名在第一个非空格
                    name = nonempty[0]
                tail = [c for c in cells[cells.index(name) + 1:] if c] if name in cells else nonempty[1:]
                title = tail[0] if tail else ""
                major = tail[1] if len(tail) > 1 else ""
                unit = tail[2] if len(tail) > 2 else ""
                # 签名栏常为空；单位栏缺失时 tail[2] 可能是空，不用管
                if 2 <= len(name) <= 4 and not re.search(r"[0-9]{4}", name):
                    out["personnel"].append(
                        {"name": name, "title": title, "major": major, "unit": unit})
            continue
        if not in_company:
            continue
        for label, field in _LABEL_MAP.items():
            if label in "".join(cells):
                v = _val_after(row, label)
                if v and not out.get(field):
                    out[field] = v
    return out


def extract_from_pdf(path: str) -> Dict[str, Any]:
    """从 PDF 模板的报告表页抽字段（只扫前 90 页）。"""
    try:
        import pdfplumber
    except Exception:
        return {}
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:90]:
                text = page.extract_text() or ""
                if "主要编制人员" not in text and "资质证书名称" not in text:
                    continue
                for table in page.extract_tables() or []:
                    got = _extract_from_rows(table)
                    if got.get("personnel") or got.get("name") or got.get("qualification_text"):
                        return got
    except Exception as e:
        logger.warning(f"[人员导入] PDF 解析失败 {path}: {e}")
    return {}


def extract_from_docx(path: str) -> Dict[str, Any]:
    """从 docx 模板的表格里抽字段。"""
    try:
        from docx import Document
    except Exception:
        return {}
    try:
        doc = Document(str(path))
        for table in doc.tables:
            rows = [[c.text for c in r.cells] for r in table.rows]
            got = _extract_from_rows(rows)
            if got.get("personnel") or got.get("name") or got.get("qualification_text"):
                return got
    except Exception as e:
        logger.warning(f"[人员导入] DOCX 解析失败 {path}: {e}")
    return {}


def extract_from_template(path: str) -> Dict[str, Any]:
    ext = Path(str(path)).suffix.lower()
    if ext == ".pdf":
        return extract_from_pdf(path)
    if ext in (".docx", ".docm"):
        return extract_from_docx(path)
    return {}


def read_stats_file(path: str) -> List[Dict[str, str]]:
    """读人员统计表 → [{name,title,major,unit}]。

    支持 xlsx/xlsm（首行表头）与 csv。表头认：
    姓名 / 职称（或 职称/职务、职务）/ 专业 / 单位。
    """
    p = Path(str(path or ""))
    if not p.exists():
        logger.warning(f"[人员导入] 统计表不存在: {p}")
        return []
    rows: List[List[Any]] = []
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), data_only=True)
            ws = wb.worksheets[0]
            rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        except Exception as e:
            logger.warning(f"[人员导入] Excel 读取失败: {e}")
            return []
    elif p.suffix.lower() == ".csv":
        try:
            with open(p, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        except Exception as e:
            logger.warning(f"[人员导入] CSV 读取失败: {e}")
            return []
    else:
        logger.warning(f"[人员导入] 不支持的统计表格式: {p.suffix}")
        return []
    if not rows:
        return []
    header = [_cell(c) for c in rows[0]]
    idx = {}
    for i, h in enumerate(header):
        if "姓名" in h:
            idx["name"] = i
        elif "职称" in h or "职务" in h:
            idx["title"] = i
        elif "专业" in h:
            idx["major"] = i
        elif "单位" in h:
            idx["unit"] = i
    if "name" not in idx:
        logger.warning("[人员导入] 统计表缺少「姓名」列")
        return []
    out = []
    for r in rows[1:]:
        name = _cell(r[idx["name"]]) if idx["name"] < len(r) else ""
        if not name:
            continue
        out.append({
            "name": name,
            "title": _cell(r[idx["title"]]) if "title" in idx and idx["title"] < len(r) else "",
            "major": _cell(r[idx["major"]]) if "major" in idx and idx["major"] < len(r) else "",
            "unit": _cell(r[idx["unit"]]) if "unit" in idx and idx["unit"] < len(r) else "",
        })
    return out


# ═══════════════════════════════════════════════════════════════
# 入库
# ═══════════════════════════════════════════════════════════════
def _db(path: str) -> Optional[sqlite3.Connection]:
    p = Path(str(path or ""))
    if not p.exists():
        logger.warning(f"[人员导入] 数据库不存在: {p}")
        return None
    return sqlite3.connect(str(p))


def upsert_personnel(db_path: str, company_id: int,
                     people: Sequence[Dict[str, str]]) -> Dict[str, int]:
    """按 (姓名, 公司) 补人员职称/专业 —— **只补空，不覆盖**。"""
    conn = _db(db_path)
    if not conn or not company_id:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    ins = upd = skip = 0
    try:
        cur = conn.cursor()
        for p in people or []:
            name = _cell(p.get("name"))
            if not name:
                continue
            row = cur.execute(
                "SELECT id, title, major FROM company_personnel "
                "WHERE name = ? AND company_id = ?", (name, int(company_id))).fetchone()
            title = _cell(p.get("title"))
            major = _cell(p.get("major"))
            if row:
                sets, params = [], []
                if title and not (row[1] or "").strip():
                    sets.append("title = ?"); params.append(title)
                if major and not (row[2] or "").strip():
                    sets.append("major = ?"); params.append(major)
                if sets:
                    params.append(row[0])
                    cur.execute(f"UPDATE company_personnel SET {', '.join(sets)} WHERE id = ?",
                                params)
                    upd += 1
                else:
                    skip += 1
            else:
                cur.execute(
                    "INSERT INTO company_personnel (name, major, title, role, company_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, major, title, "项目组成员", int(company_id)))
                ins += 1
        conn.commit()
    finally:
        conn.close()
    return {"inserted": ins, "updated": upd, "skipped": skip}


def upsert_company_profile(db_path: str, company_id: int, data: Dict[str, Any]) -> Dict[str, int]:
    """把模板里的公司资质字段写进 companies —— **只补空，不覆盖**。"""
    conn = _db(db_path)
    if not conn or not company_id:
        return {"updated": 0}
    fields = {
        "qualification_text": _cell(data.get("qualification_text")),
        "qualification_level": _cell(data.get("qualification_level")),
        "qualification_authority": _cell(data.get("qualification_authority")),
        "qualification_no": _cell(data.get("qualification_no")),
        "contact": _cell(data.get("contact")),
        "phone": _cell(data.get("phone")),
        "leader_name": _cell(data.get("leader_name")),
    }
    # 防老库没这些列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
    fields = {k: v for k, v in fields.items() if v and k in cols}
    if not fields:
        conn.close()
        return {"updated": 0}
    sets = ", ".join(f"{k} = CASE WHEN IFNULL({k}, '') = '' THEN ? ELSE {k} END"
                     for k in fields)
    conn.execute(f"UPDATE companies SET {sets} WHERE id = ?",
                 [*fields.values(), int(company_id)])
    conn.commit()
    conn.close()
    return {"updated": 1}


def import_from_templates(storage_dir: str, auth_db: str, knowledge_db: str,
                          template_paths: Sequence[str]) -> Dict[str, Any]:
    """逐份模板抽数据：公司字段进**同名公司**，人员按姓名补到所有内部公司。"""
    conn = _db(auth_db)
    if not conn:
        return {"templates": 0, "personnel": []}
    try:
        companies = list(conn.execute(
            "SELECT id, name, is_internal FROM companies").fetchall())
    finally:
        conn.close()
    by_name = {str(n or "").strip(): (cid, bool(internal)) for cid, n, internal in companies}
    internal_ids = [cid for cid, _n, internal in companies if internal]

    summary = {"templates": 0, "companies": [], "personnel": []}
    for p in template_paths:
        data = extract_from_template(p)
        if not data:
            continue
        summary["templates"] += 1
        cname = _cell(data.get("name"))
        matched = by_name.get(cname)
        if matched and any(data.get(k) for k in (
                "qualification_text", "qualification_level", "qualification_authority",
                "qualification_no", "contact", "phone", "leader_name")):
            upsert_company_profile(auth_db, matched[0], data)
            summary["companies"].append({"id": matched[0], "name": cname})
        people = data.get("personnel") or []
        # 人员是共享的：补到模板同名公司 + 所有内部公司
        targets = set(internal_ids)
        if matched:
            targets.add(matched[0])
        for cid in sorted(targets):
            r = upsert_personnel(knowledge_db, cid, people)
            summary["personnel"].append({"company_id": cid, **r})
    return summary


def import_stats_file(path: str, company_id: int, knowledge_db: str) -> Dict[str, Any]:
    people = read_stats_file(path)
    r = upsert_personnel(knowledge_db, company_id, people)
    return {"people": len(people), **r}
