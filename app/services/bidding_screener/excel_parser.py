"""千里马招标 excel 解析器 — 逐行转结构化记录，自动识别列名。

千里马导出的 excel 列名可能有变体（如"项目名称"/"采购项目"/"公告标题"），
通过列名映射表自动识别，不硬编码固定列序。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# 目标字段 → 列名变体（按优先级，先匹配到的生效）
COLUMN_MAP: Dict[str, List[str]] = {
    "name": ["项目名称", "采购项目", "项目", "公告标题", "标题", "项目标题", "标的名"],
    "region": ["地区", "省份", "省市", "所属地区", "地点", "区域", "所在地区", "城市"],
    "type": ["招标类型", "采购方式", "项目类型", "类型", "采购类型", "工程货物服务"],
    "buyer": ["招标人", "采购人", "业主", "招标单位", "采购单位", "招标方", "采购方"],
    "budget": ["预算金额", "采购预算", "控制价", "金额", "投资额", "预算", "采购金额", "控制金额"],
    "qualification": ["资质要求", "资格条件", "投标人资格", "特殊资质", "资格要求", "资质", "资格条件"],
    "publish_date": ["发布日期", "发布时间", "公告时间", "发布日期时间", "时间"],
    "link": ["链接", "网址", "详情链接", "URL", "招标公告链接", "公告链接", "详情"],
}


def _normalize_header(h: str) -> str:
    """规范化表头：去空白、去换行、去冒号。"""
    return (h or "").strip().replace("\n", "").replace("：", "").replace(":", "")


def detect_column_indexes(headers: List[str]) -> Dict[str, int]:
    """从表头行识别目标字段对应的列索引。

    Returns: {目标字段: 列索引}
    """
    mapping = {}
    for field, variants in COLUMN_MAP.items():
        for idx, header in enumerate(headers):
            norm = _normalize_header(str(header))
            if not norm:
                continue
            # 精确匹配 或 包含匹配（变体）
            for variant in variants:
                if norm == variant or variant in norm or norm in variant:
                    if field not in mapping:  # 第一个匹配优先
                        mapping[field] = idx
                    break
    return mapping


def parse_excel(file_path: str) -> List[Dict]:
    """解析千里马导出的 excel，返回结构化记录列表。

    Args:
        file_path: excel 文件路径（.xlsx/.xls/.csv）

    Returns:
        [{name, region, type, buyer, budget, qualification, publish_date, link, raw}, ...]
        raw 保存原始行数据（单元格文本，供追溯）
    """
    ext = Path(file_path).suffix.lower()

    if ext in (".xlsx", ".xlsm"):
        return _parse_xlsx(file_path)
    elif ext == ".csv":
        return _parse_csv(file_path)
    else:
        # .xls 老格式：尝试 openpyxl，失败提示
        return _parse_xlsx(file_path)


def _parse_xlsx(file_path: str) -> List[Dict]:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    # 表头：跳过前面的空行/标题行，找到含"项目名称/标题"等关键字的行
    header_row_idx = _find_header_row(rows)
    if header_row_idx is None:
        # 默认第一行是表头
        header_row_idx = 0

    headers = [str(h) if h is not None else "" for h in rows[header_row_idx]]
    col_map = detect_column_indexes(headers)

    if not col_map:
        logger.warning(f"未能识别 excel 表头: {headers[:10]}")
        return []

    records = []
    for row in rows[header_row_idx + 1:]:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        record = {"raw": {headers[i]: str(row[i]) if i < len(row) and row[i] is not None else ""
                          for i in range(len(headers))}}
        for field, idx in col_map.items():
            if idx < len(row) and row[idx] is not None:
                record[field] = str(row[idx]).strip()
        # 至少要有项目名称才保留
        if record.get("name"):
            records.append(record)

    return records


def _find_header_row(rows: List) -> Optional[int]:
    """定位表头行：含多个已知列名关键词的行。"""
    for i, row in enumerate(rows[:10]):  # 只在前10行找
        if not row:
            continue
        cells = [str(c) for c in row if c is not None]
        matched = sum(
            1 for cell in cells
            if any(variant in cell for variant in
                   ["项目名称", "标题", "招标人", "采购人", "地区", "金额", "链接"])
        )
        if matched >= 2:
            return i
    return None


def _parse_csv(file_path: str) -> List[Dict]:
    import csv
    with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return []

    header_row_idx = _find_header_row(rows) or 0
    headers = [str(h) for h in rows[header_row_idx]]
    col_map = detect_column_indexes(headers)

    if not col_map:
        return []

    records = []
    for row in rows[header_row_idx + 1:]:
        if not row or all(not c.strip() for c in row):
            continue
        record = {"raw": {headers[i]: row[i] if i < len(row) else ""
                          for i in range(len(headers))}}
        for field, idx in col_map.items():
            if idx < len(row):
                record[field] = row[idx].strip()
        if record.get("name"):
            records.append(record)
    return records
