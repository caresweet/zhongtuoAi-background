# -*- coding: utf-8 -*-
"""第3章图位：系统卫星位置图优先，原始红线渲染只能进附件。

🔴 线上 2026-11-03：图3-1 又变成白底红线图。根因不是卫星图没生成，
   而是图片目录「正文每类最多 2 张」按输入顺序截断 —— 用户上传的
   `.shp.png` / `.cad.png` 先把位置图名额吃光，卫星图被挤掉；随后章末兜底
   又把那两张红线图插回正文。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.report_assembler import (      # noqa: E402
    drop_raw_redline_from_ch3, split_raw_redline,
)

_SAT = {"path": "images/图3-1 工程位置示意图.jpg", "label": "项目地理位置示意图"}
_RAW1 = {"path": "images/井神红线0907.shp.png", "label": "项目地理位置示意图"}
_RAW2 = {"path": "images/井神红线0907.cad.png", "label": "项目地理位置示意图"}


def test_split_keeps_system_map_and_drops_raw_renders():
    keep, raw = split_raw_redline([_RAW1, _SAT, _RAW2])
    assert keep == [_SAT]
    assert raw == [_RAW1, _RAW2]


def test_drop_raw_redline_from_chapter_three():
    by_ch = {3: [_RAW1, _SAT, _RAW2]}
    assert drop_raw_redline_from_ch3(by_ch) == 2
    assert by_ch[3] == [_SAT]


def test_drop_handles_string_chapter_key():
    by_ch = {"3": [_RAW1, _SAT]}
    assert drop_raw_redline_from_ch3(by_ch) == 1
    assert by_ch["3"] == [_SAT]


def test_no_change_when_no_raw_render():
    by_ch = {3: [_SAT]}
    assert drop_raw_redline_from_ch3(by_ch) == 0
    assert by_ch[3] == [_SAT]
