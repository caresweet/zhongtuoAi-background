# -*- coding: utf-8 -*-
"""投标文件版式配置（公司体例），不从某一份招标文件里写死。

业主 2026-11-11：「所有的修改规格方面都不要写死，都是会变动的」。
所以版式参数集中在 `storage/bidding_style.json`（不存在则用下面的**公司默认体例**），
按公司/项目可整份替换；代码里只读配置，不再散落字号、边距、图片宽度。

默认值取自公司人工投标文件体例（《投标文件-众拓测绘-佳富村》）：
  · 页边距 2.5/2.5/2.4/2.8cm
  · 封面 36 磅宋体加粗标题 + 18 磅加粗三行
  · 一级标题 黑体 16 磅加粗居中；二级 宋体 15 磅加粗居中；三级 宋体 15 磅加粗左对齐
  · 正文 宋体 14 磅、首行缩进 0.87cm、固定行距 25 磅
  · 表格 宋体 12 磅、表头加粗
  · 目录 宋体 20 磅标题 + 14 磅条目、33 磅行距
  · 身份证/证书类图片宽 7.5cm，截图 15.8cm，财务报表 16cm
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_STYLE: Dict[str, Any] = {
    "page": {"margin_top_cm": 2.5, "margin_bottom_cm": 2.5,
             "margin_left_cm": 2.4, "margin_right_cm": 2.8},
    "cover": {"title": "投 标 文 件", "title_font": "宋体", "title_size": 36,
              "title_bold": True, "line_font": "宋体", "line_size": 18,
              "line_bold": True, "date_format": "%Y.%-m.%-d",
              "show_zhengben": False, "show_seal_note": False},
    "heading1": {"font": "黑体", "size": 16, "bold": True, "align": "center"},
    "heading2": {"font": "宋体", "size": 15, "bold": True, "align": "center"},
    "heading3": {"font": "宋体", "size": 15, "bold": True, "align": "left"},
    "body": {"font": "宋体", "size": 14, "first_line_cm": 0.87, "line_pt": 25},
    "table": {"font": "宋体", "size": 12, "header_bold": True},
    "toc": {"title": "投标主要文件目录", "title_font": "宋体", "title_size": 20,
            "title_bold": True,
            "entry_font": "宋体", "entry_size": 14, "line_pt": 33,
            "show_page_numbers": False},
    "images": {"id_card_cm": 7.0, "cert_cm": 7.5, "screenshot_cm": 15.8,
               "financial_cm": 16.0},
}

_CACHE: Dict[str, Any] = {}


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_bidding_style(storage_dir: str | Path) -> Dict[str, Any]:
    """读公司体例：`storage/bidding_style.json` 覆盖默认值（整份可替换）。"""
    key = str(storage_dir)
    if key in _CACHE:
        return _CACHE[key]
    style = DEFAULT_STYLE
    p = Path(storage_dir) / "bidding_style.json"
    if p.exists():
        try:
            style = _deep_merge(DEFAULT_STYLE, json.loads(p.read_text("utf-8")))
            logger.info(f"[投标版式] 使用 {p}")
        except Exception as e:
            logger.warning(f"[投标版式] {p} 读取失败，用默认体例: {e}")
    _CACHE[key] = style
    return style
