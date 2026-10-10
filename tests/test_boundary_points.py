# -*- coding: utf-8 -*-
"""界址点 → 面积（确定性路径）。

用**真实的勘测定界界址点文件**验证：算出的面积必须与勘测定界报告写的一致 ——
这是「面积以勘测定界为准」不依赖 OCR/LLM 的实现。
"""

from app.services.boundary_points import (
    polygon_area_m2, parse_boundary_points, read_boundary_file,
)

# 明远佳苑真实界址点（GBK 点号列已被解成乱码也不影响 —— 本模块只认坐标）
_REAL = (
    "1\t兴\t3714615.396 \t兴\t40413266.562 \n"
    "2\t兴\t3714614.732 \t兴\t40413275.808 \n"
    "3\t兴\t3714614.382 \t兴\t40413275.641 \n"
)
# 一个 100m × 100m 的正方形，面积必然是 10000 ㎡
_SQUARE = (
    "1 1 0.0 0.0\n2 1 100.0 0.0\n3 1 100.0 100.0\n4 1 0.0 100.0\n1 1 0.0 0.0\n"
)


def test_parse_ignores_non_numeric_columns():
    pts = parse_boundary_points(_REAL)
    assert pts == [(3714615.396, 40413266.562),
                   (3714614.732, 40413275.808),
                   (3714614.382, 40413275.641)]


def test_closing_point_is_dropped():
    """`.dat` 末点常与首点重复，要去掉，否则鞋带公式会算错。"""
    pts = parse_boundary_points(_SQUARE)
    assert len(pts) == 4
    assert pts[0] != pts[-1]


def test_square_area():
    assert polygon_area_m2([(0, 0), (100, 0), (100, 100), (0, 100)]) == 10000.0


def test_too_few_points_is_zero():
    assert polygon_area_m2([(0, 0), (1, 1)]) == 0.0


def test_read_real_boundary_file():
    """真实文件：面积应与勘测定界报告写的 8070 ㎡ 吻合（容差 2%）。"""
    import pathlib
    p = pathlib.Path("/Users/mac/Downloads/明远佳苑/临时用地--勘测定界报告图/明远佳苑.dat.txt")
    if not p.exists():
        import pytest
        pytest.skip("测试材料不在本机")
    r = read_boundary_file(str(p))
    assert r["count"] == 41
    assert abs(r["area_m2"] - 8070.0) <= 8070.0 * 0.02
    assert abs(r["area_mu"] - 12.10) <= 0.1


def test_missing_file_is_empty_not_exception():
    r = read_boundary_file("/no/such/file.dat")
    assert r["count"] == 0 and r["area_m2"] == 0.0
