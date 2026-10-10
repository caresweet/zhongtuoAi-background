#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从**真实几何数据**生成复垦报告的图件。

数据源（业主材料，实测）：
    临时用地界址点  临时用地--勘测定界报告图/明远佳苑.dat.txt    （42 点，X=北 Y=东）
    主体项目红线    主体红线/主体项11111目红线2000120.dxf        （闭合多段线，X=东 Y=北）

坐标口径已核对：红线 DXF 的 Y 范围 3714126~3714614 正是界址点的 X 范围；
红线 X 413250~413498 与界址点 Y（去掉 40 带号 → 413231~413314）同区间。
**∴ 两者在同一坐标系，可以直接叠加** —— 临时用地就压在主体红线的西侧边界上。

用法：
    cd backend && python3 scripts/gen_reclamation_figures.py
产物：
    ../docs/figures/*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib import font_manager                   # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.boundary_points import (            # noqa: E402
    polygon_area_m2, read_boundary_file, read_dxf_polygons,
)

MAT = Path("/Users/mac/Downloads/明远佳苑")
OUT = BACKEND.parent / "docs" / "figures"

for _f in ("PingFang SC", "Heiti SC", "Songti SC", "Arial Unicode MS"):
    if _f in {x.name for x in font_manager.fontManager.ttflist}:
        plt.rcParams["font.sans-serif"] = [_f]
        break
plt.rcParams["axes.unicode_minus"] = False


def _load():
    """取两组几何，统一成 (东坐标, 北坐标)。"""
    b = read_boundary_file(str(MAT / "临时用地--勘测定界报告图/明远佳苑.dat.txt"))
    # 界址点文件是「X=北 Y=东」，且东坐标带 40 带号 → 还原成 (东, 北)
    temp = [(y - 40000000.0, x) for x, y in b["points"]]
    red = read_dxf_polygons(str(MAT / "主体红线/主体项11111目红线2000120.dxf"))
    return b, temp, red


def _north_arrow(ax):
    """右上角指北针（用**画布比例坐标**画 —— 不能用数据坐标，
    否则 annotate/plot 会把数据范围撑爆，图就变形成一条缝）。"""
    ax.annotate("", xy=(0.93, 0.945), xytext=(0.93, 0.875),
                xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color="#333", lw=1.8))
    ax.text(0.93, 0.952, "北", ha="center", va="bottom", fontsize=11,
            color="#333", transform=ax.transAxes)


def _scale_bar(ax, meters=50):
    """左下角比例尺。长度按**真实米数**折算成画布比例，画在轴下方。"""
    x0, x1 = ax.get_xlim()
    w_frac = min(0.35, meters / max(1.0, (x1 - x0)))
    sx, sy = 0.04, -0.028
    ax.plot([sx, sx + w_frac], [sy, sy], transform=ax.transAxes,
            color="#333", lw=3, solid_capstyle="butt", clip_on=False)
    ax.text(sx + w_frac / 2, sy - 0.008, f"{meters} m", transform=ax.transAxes,
            ha="center", va="top", fontsize=9, color="#333")


def _zoom_inset(ax, pts, title):
    """在主图右下角开一个放大窗（临时用地是细长条，主图里看不清）。"""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx = (max(xs) - min(xs)) * 0.18 or 1.0
    my = (max(ys) - min(ys)) * 0.06 or 1.0
    iax = ax.inset_axes([0.58, 0.05, 0.39, 0.30])
    iax.add_patch(MplPolygon(pts, closed=True, facecolor="#FDE9E9",
                             edgecolor="#C00000", lw=1.6))
    iax.plot(xs, ys, "o", ms=1.6, color="#C00000")
    iax.set_xlim(min(xs) - mx, max(xs) + mx)
    iax.set_ylim(min(ys) - my, max(ys) + my)
    iax.set_xticks([]); iax.set_yticks([])
    iax.set_title(title, fontsize=10, pad=4)
    for s in iax.spines.values():
        s.set_color("#C00000")


def _frame(ax, title):
    ax.set_title(title, fontsize=15, pad=12)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#999")


def fig_location(temp, red):
    """图3-1 工程位置示意图：主体红线 + 临时用地 的相对位置。"""
    fig, ax = plt.subplots(figsize=(9, 11))
    poly = _dxf_ring(str(MAT / "主体红线/主体项11111目红线2000120.dxf"))
    if poly:
        ax.add_patch(MplPolygon(poly, closed=True, facecolor="#DCE6F1",
                                edgecolor="#2E75B6", lw=2, zorder=1))
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        ax.text(cx, cy, "主体项目红线\n（二期）9.6414 公顷",
                ha="center", va="center", fontsize=12, color="#1F4E79", zorder=3)
    ax.add_patch(MplPolygon(temp, closed=True, facecolor="#FDE9E9",
                            edgecolor="#C00000", lw=2.2, zorder=4))
    tx = sum(p[0] for p in temp) / len(temp)
    ty = sum(p[1] for p in temp) / len(temp)
    ax.annotate("临时用地\n0.8070 公顷（12.10 亩）\n复垦责任范围",
                xy=(tx, ty), xytext=(0.06, 0.34), textcoords="axes fraction",
                fontsize=12, color="#C00000",
                arrowprops=dict(arrowstyle="->", color="#C00000", lw=1.4))
    ax.autoscale_view()          # 先定住数据范围，比例尺换算才对
    _zoom_inset(ax, temp, "临时用地（放大）")
    _north_arrow(ax); _scale_bar(ax, 50)
    _frame(ax, "图3-1  工程位置示意图")
    fig.tight_layout()
    return fig


def _dxf_ring(path):
    import ezdxf
    try:
        d = ezdxf.readfile(path)
    except Exception:
        return None
    for e in d.modelspace():
        if e.dxftype() == "LWPOLYLINE" and e.closed:
            return [(float(x), float(y)) for x, y in e.get_points("xy")]
    return None


def fig_damage(temp, title, note):
    """损毁预测图 / 复垦规划图：同一底图 + 各自的标注。"""
    fig, ax = plt.subplots(figsize=(8, 10))
    ax.add_patch(MplPolygon(temp, closed=True, facecolor="#FDE9E9",
                            edgecolor="#C00000", lw=2.2))
    cx = sum(p[0] for p in temp) / len(temp)
    cy = sum(p[1] for p in temp) / len(temp)
    ax.text(cx, cy, note, ha="center", va="center", fontsize=12, color="#833C00",
            bbox=dict(boxstyle="round,pad=0.5", fc="#FFF2CC", ec="#BF8F00"))
    # 界址点
    ax.plot([p[0] for p in temp], [p[1] for p in temp], "o", ms=2.5, color="#C00000")
    ax.autoscale_view()
    _north_arrow(ax); _scale_bar(ax, 50)
    _frame(ax, title)
    fig.tight_layout()
    return fig


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    b, temp, red = _load()
    print(f"临时用地 {len(temp)} 点，面积 {polygon_area_m2(temp):.2f} ㎡；红线 {len(red)} 段")

    fig_location(temp, red).savefig(OUT / "图3-1_工程位置示意图.png", dpi=200)
    plt.close("all")

    fig_damage(temp, "图件③  临时用地土地复垦损毁预测图",
               "临时用地 0.8070 公顷\n损毁方式：压占\n损毁程度：重度").savefig(
        OUT / "图件③_土地复垦损毁预测图.png", dpi=200)
    plt.close("all")

    fig_damage(temp, "图件④  临时用地土地复垦规划图",
               "复垦责任范围 0.8070 公顷\n复垦方向：恢复原地类\n复垦率 100%").savefig(
        OUT / "图件④_土地复垦规划图.png", dpi=200)
    plt.close("all")

    # 图4-1 的「平面布置图」半张（另一半是现场照片，材料里没有）
    fig_damage(temp, "临时用地平面布置图",
               "施工便道 + 材料堆场\n临时用地 0.8070 公顷").savefig(
        OUT / "图4-1_临时用地平面布置图.png", dpi=200)
    plt.close("all")

    # 图件②「临时用地土地利用现状图」= 勘测定界地类图，材料里直接有，原样复制
    import shutil
    src = MAT / "临时用地--勘测定界报告图/现状图.jpg"
    if src.exists():
        dst = OUT / "图件②_临时用地土地利用现状图.jpg"
        shutil.copyfile(src, dst)
        print(f"✅ {dst.name}  ({dst.stat().st_size // 1024} KB，材料原图)")
    else:
        print("⚠️  图件② 源图（现状图.jpg）不在材料里")

    for p in sorted(OUT.glob("*.png")):
        print(f"✅ {p.name}  ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
