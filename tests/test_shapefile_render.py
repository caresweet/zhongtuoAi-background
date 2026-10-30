# -*- coding: utf-8 -*-
"""Shapefile 出图的测试。

业主 2026-10-30：Shapefile 整套能上传之后，报告里的红线图还得能画出来。
这里钉住「面/线/点都能出图、坏文件不抛异常」。
"""
import os

import shapefile

from app.services.shapefile_renderer import render_shapefile_to_png


def _write_polygon(tmp_path):
    w = shapefile.Writer(str(tmp_path / "poly"), shapeType=shapefile.POLYGON)
    w.field("name", "C", size=20)
    w.poly([[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]])
    w.record("地块1")
    w.close()
    return str(tmp_path / "poly.shp")


def _write_polyline(tmp_path):
    w = shapefile.Writer(str(tmp_path / "line"), shapeType=shapefile.POLYLINE)
    w.field("name", "C", size=20)
    w.line([[[0, 0], [5, 5], [10, 0]]])
    w.record("道路")
    w.close()
    return str(tmp_path / "line.shp")


def _write_points(tmp_path):
    w = shapefile.Writer(str(tmp_path / "pts"), shapeType=shapefile.POINT)
    w.field("name", "C", size=20)
    for x, y in ((1, 1), (4, 5), (9, 2)):
        w.point(x, y)
        w.record(f"P{x}")
    w.close()
    return str(tmp_path / "pts.shp")


def test_polygon_renders(tmp_path):
    shp = _write_polygon(tmp_path)
    out = str(tmp_path / "poly.png")
    ok, msg = render_shapefile_to_png(shp, out)
    assert ok, msg
    assert os.path.getsize(out) > 1024
    assert "面 1" in msg


def test_polyline_renders(tmp_path):
    ok, msg = render_shapefile_to_png(_write_polyline(tmp_path), str(tmp_path / "line.png"))
    assert ok, msg
    assert "线 1" in msg


def test_points_render(tmp_path):
    ok, msg = render_shapefile_to_png(_write_points(tmp_path), str(tmp_path / "pts.png"))
    assert ok, msg
    assert "点 3" in msg


def test_missing_or_wrong_file_returns_false(tmp_path):
    ok, why = render_shapefile_to_png(str(tmp_path / "nope.shp"), str(tmp_path / "x.png"))
    assert ok is False and "不存在" in why

    bad = tmp_path / "bad.dxf"
    bad.write_text("0\nSECTION\n")
    ok2, why2 = render_shapefile_to_png(str(bad), str(tmp_path / "y.png"))
    assert ok2 is False and "不是 Shapefile" in why2
