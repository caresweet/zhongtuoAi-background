# -*- coding: utf-8 -*-
"""复垦图件：从界址点坐标表生成正文配图（确定性，无 LLM）。

护栏的重点不是「画得好看」，而是**别把残表当真**：PDF 表格跨页时文本层会把多页
拼成一段，实测主体项目那张表拼出了「序号回绕、同一坐标连续重复 20 次」的残表
（假面积 9470 ㎡，真值 96413.6 ㎡）—— 按它画出来就是一条凭空捏造的红线。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reclamation_figures import (      # noqa: E402
    collect_survey_polygons, ensure_reclamation_figures, survey_polygons,
)

# 临时用地表原样（勘测定界报告文本层）：序号 点号 类别 X(北) Y(东) 材料 高程
_TEMP = """\
界址点坐标表
1 J1 1 3714615.396 40413266.562 喷涂 9.27
2 J2 1 3714614.732 40413275.808 喷涂 0.39
3 J3 1 3714614.382 40413275.641 喷涂 13.71
4 J4 1 3714600.709 40413274.581 喷涂 0.05
5 J5 1 3714600.302 40413274.143 喷涂 11.83
6 J6 1 3714588.442 40413265.856 喷涂 0.34
"""

# 主体项目表：跨页被拼成一段 → 序号回绕 + 连续重复点（残表）
_BROKEN = """\
界址点坐标表 119
4 3714132.075 40413212.886 0.43
5 3714132.075 40413213.292 21.00
6 3714143.721 40413230.864 0.50
7 3714143.592 40413231.304 30.00
8 3714180.162 40413256.599 0.00
1 3714210.758 40413473.140 0.00
2 3714210.758 40413473.140 0.00
3 3714210.758 40413473.140 0.00
"""


def test_parses_clean_coordinate_table():
    """干净表 → 一个多边形；坐标换算成 (东, 北)，东坐标去 40 带号。"""
    polys = survey_polygons(_TEMP)
    assert len(polys) == 1
    p = polys[0]
    assert p["count"] == 6
    assert p["points"][0] == pytest.approx((413266.562, 3714615.396))
    assert p["area_m2"] > 0


def test_rejects_garbled_cross_page_table():
    """残表（序号回绕 / 连续重复点）整段弃用 —— 宁可不出图，不许画假红线。"""
    assert survey_polygons(_BROKEN) == []


def test_run_splits_on_non_clean_line():
    """残表与干净表混在一份材料里时，只收干净那段。"""
    polys = survey_polygons(_BROKEN + "\n合计 96413.6\n" + _TEMP)
    assert len(polys) == 1
    assert polys[0]["count"] == 6


def test_collect_dedups_and_sorts_by_area():
    """跨材料收集：同一张表出现两次只收一次；按面积升序（最小 = 复垦责任范围）。"""
    mats = [{"source_name": "a.pdf", "text_content": _TEMP},
            {"source_name": "b.pdf", "text_content": _TEMP},
            {"source_name": "c.pdf", "text_content": _BROKEN}]
    polys = collect_survey_polygons(mats)
    assert len(polys) == 1


def test_no_geometry_means_no_figure(tmp_path):
    """材料里没有可信几何 → 不生成图、不报错（报告照出，只是无配图）。"""
    assert ensure_reclamation_figures([], str(tmp_path)) == []
    assert ensure_reclamation_figures([{"source_name": "x", "text_content": _BROKEN}],
                                      str(tmp_path)) == []


def test_generates_landscape_figure(tmp_path):
    """出图落盘，且**宽 > 高** —— 装配器有条「竖版地图转 90° 横放」的启发式，
    自己的生成图若画成竖版会被一并转躺（连图题一起歪）。"""
    gen = ensure_reclamation_figures(
        [{"source_name": "勘测定界报告.pdf", "text_content": _TEMP}], str(tmp_path))
    assert len(gen) == 1
    # 🔴 文件名必须**逐字**等于 original_name：图片目录解析相对路径时回退到
    #    `storage/images/<original_name>`，名字不符就会被当不存在丢掉。
    assert Path(gen[0]["original_name"]).name == gen[0]["original_name"]
    assert gen[0]["path"] == f"images/{gen[0]['original_name']}"
    png = Path(tmp_path) / "images" / gen[0]["original_name"]
    assert png.exists() and png.stat().st_size > 0

    PILImage = pytest.importorskip("PIL.Image")
    w, h = PILImage.open(png).size
    assert w > h


def test_figure_name_carries_the_binding_keyword():
    """图名里必须带「位置示意图」—— 图片目录按这个关键词把它绑到正文第3章
    （见 `reclamation_definitions.RECLAMATION_IMAGE_SPECS[3]` 的 keywords）。"""
    assert "位置示意图" in "图3-1 工程位置示意图.png"


def test_generic_photo_binds_to_ch4_via_alias(tmp_path):
    """无关键词的照片名（IMG_xxxx / 扫描仪默认名）也要能进正文第4章。

    复垦规格的 `type` 是按图题细分的专名（`site_photo`），而分类器只产出粗类
    （`photo` / `other`）—— 不写 `aliases` 就**永远**绑不上，用户上传的照片
    只能落附件，正文仍是干文字（这正是「图片没进报告」的一条成因）。
    """
    PILImage = pytest.importorskip("PIL.Image")
    from app.services.image_catalog import build_image_catalog
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    items = []
    for i, n in enumerate(("IMG_2043.jpg", "MX-M3508N_001.jpg")):
        p = img_dir / n
        PILImage.new("RGB", (60, 40), (200 - i * 40, 180, 160)).save(p)
        items.append({"path": str(p), "original_name": n})
    cat = build_image_catalog(items, domain="reclamation")
    assert {c["category"] for c in cat["catalog"]} == {"other"}
    assert len(cat["by_chapter"].get(4, [])) == 2
    assert cat["by_chapter"][4][0]["label"] == "临时用地现场照片"
