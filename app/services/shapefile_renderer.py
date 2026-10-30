# -*- coding: utf-8 -*-
"""Shapefile → PNG 渲染（GIS 矢量数据出图）。

**为什么需要**：土地复垦/土地征收的「临时用地红线图」「用地范围图」，
甲方给的原始成果经常就是一整套 Shapefile（`井神红线0907.shp/.shx/.dbf/.prj…`）。
2026-10-30 起这套文件已经能上传留存（见 `file_service.save_attachment` 的 vector 类型），
但报告里要的是**图片** —— 这里把 `.shp` 的几何画成 PNG，接进既有的图片流水线。

| 输入 | 处理 |
|---|---|
| 面（polygon, 5/15/25） | 浅红填充 + 深红边界（红线图惯例），内环按洞处理 |
| 线（polyline, 3/13/23） | 蓝色折线 |
| 点（point, 1/11/21） | 红色圆点 |

🔴 **只画几何、不落文字**：属性表（.dbf）里的地块号/权属人字段各项目五花八门，
   硬画容易画出「FID: 0」这类没意义的东西。需要标注时再按图层字段单独定。
🔴 渲染失败**不抛异常给上传接口**：shapefile 照常留存，只是拿不到预览图。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

SHAPEFILE_EXTENSIONS = (".shp",)

# 面 / 线 / 点 三类几何的 shapeType（含 Z/M 变体）
_POLYGON_TYPES = {5, 15, 25}
_POLYLINE_TYPES = {3, 13, 23}
_POINT_TYPES = {1, 11, 21, 8, 18, 28}   # 含 MultiPoint

_FILL = "#fde2e2"
_EDGE = "#d32f2f"
_LINE = "#1f4e79"
_DOT = "#d32f2f"


def render_shapefile_to_png(
    shp_path: str, out_png: str, *, dpi: int = 200, background: str = "#FFFFFF",
) -> Tuple[bool, str]:
    """把一份 `.shp` 画成 PNG。

    Returns:
        (是否成功, 说明)。**不抛异常** —— 上传流程不该因为一张图渲不出来就整单失败。
    """
    p = str(shp_path or "")
    if not p or not os.path.exists(p):
        return False, "文件不存在"
    if Path(p).suffix.lower() not in SHAPEFILE_EXTENSIONS:
        return False, f"不是 Shapefile: {Path(p).suffix}"

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        matplotlib.rcParams["font.sans-serif"] = [
            "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Noto Sans CJK SC",
            "SimHei", "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import shapefile
    except Exception as e:              # 依赖缺失
        return False, f"渲染依赖缺失: {type(e).__name__}: {e}"

    try:
        reader = shapefile.Reader(str(p), encoding="utf-8", encodingErrors="replace")
        shapes = reader.shapes()
    except Exception as e:
        logger.warning(f"[SHP] 读取失败 {os.path.basename(p)}: {e}")
        return False, f"Shapefile 读取失败: {type(e).__name__}"
    if not shapes:
        return False, "Shapefile 里没有几何要素"

    try:
        from matplotlib.patches import Polygon as MplPolygon
        from matplotlib.collections import LineCollection, PatchCollection

        patches: List = []
        lines: List[List[Tuple[float, float]]] = []
        points: List[Tuple[float, float]] = []
        xs: List[float] = []
        ys: List[float] = []

        for sh in shapes:
            pts = sh.points or []
            if not pts:
                continue
            xs.extend(pt[0] for pt in pts)
            ys.extend(pt[1] for pt in pts)

            if sh.shapeType in _POLYGON_TYPES:
                parts = list(sh.parts) + [len(pts)]
                for k in range(len(parts) - 1):
                    ring = pts[parts[k]:parts[k + 1]]
                    if len(ring) >= 3:
                        patches.append(MplPolygon(ring, closed=True))
            elif sh.shapeType in _POLYLINE_TYPES:
                parts = list(sh.parts) + [len(pts)]
                for k in range(len(parts) - 1):
                    seg = pts[parts[k]:parts[k + 1]]
                    if len(seg) >= 2:
                        lines.append(seg)
            elif sh.shapeType in _POINT_TYPES:
                points.extend((pt[0], pt[1]) for pt in pts)

        if not xs or not ys:
            return False, "Shapefile 里没有可渲染的坐标"

        fig, ax = plt.subplots(figsize=(11.69, 8.27))   # A4 横向
        fig.patch.set_facecolor(background)
        ax.set_facecolor(background)
        ax.set_axis_off()
        ax.set_aspect("equal")

        if patches:
            ax.add_collection(PatchCollection(
                patches, facecolor=_FILL, edgecolor=_EDGE, linewidths=0.8, alpha=0.85))
        if lines:
            ax.add_collection(LineCollection(lines, colors=_LINE, linewidths=0.8))
        if points:
            px, py = zip(*points)
            ax.scatter(px, py, s=6, c=_DOT, marker="o", linewidths=0)

        pad_x = (max(xs) - min(xs)) * 0.02 or 1.0
        pad_y = (max(ys) - min(ys)) * 0.02 or 1.0
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)

        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, dpi=dpi, facecolor=background, bbox_inches="tight")
        plt.close(fig)

        if not os.path.exists(out_png) or os.path.getsize(out_png) < 1024:
            return False, "渲染结果为空"
        return True, (f"已渲染 {os.path.getsize(out_png) // 1024} KB"
                      f"（面 {len(patches)} / 线 {len(lines)} / 点 {len(points)}）")
    except Exception as e:
        logger.warning(f"[SHP] 渲染失败 {os.path.basename(p)}: {e}")
        return False, f"渲染失败: {type(e).__name__}: {e}"
