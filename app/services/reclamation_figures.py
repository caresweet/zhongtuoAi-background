# -*- coding: utf-8 -*-
"""复垦报告图件 —— 从**会话材料**确定性生成正文配图（不经 LLM、不编数据）。

## 为什么要有它

复垦样本里的图（位置示意图 / 损毁预测图 / 复垦规划图）是**画出来的**，不是用户
上传的。此前只有一个独立脚本 `scripts/gen_reclamation_figures.py`，里头是开发机上的
硬编码路径（`/Users/mac/Downloads/明远佳苑`）—— 装配器根本不知道它存在，所以
**生成得再对，报告里也一张图都没有**。本模块把它接进装配链。

## 几何从哪来（确定性，不依赖 OCR / LLM）

① **界址点坐标表**：勘测定界的**文本层**里就有（「界址点坐标表 序号 X Y 边长」）。
   实测明远佳苑临时用地表 41 点 → 8068.85 ㎡，勘测定界报告写 8070.00 ㎡
   （差 1.15 ㎡ = 0.014%），与 `明远佳苑.dat.txt` 算出的 8068.88 ㎡ 亦吻合。
② **闭合多段线 DXF**：见 `boundary_points.read_dxf_polygons`。

## 一条硬判据：烂表整段弃用

PDF 表格跨页时，文本层会把多页**拼成一段**。实测主体项目那张表就拼成了
「序号回绕到 1、同一坐标连续重复 20 次」的残表，按它算出的假面积 9470 ㎡
（真值 96413.6 ㎡）—— 画出来会是一条**凭空捏造的红线**。
故：**序号未严格递增、或存在连续重复点**的段，一律不收。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.boundary_points import polygon_area_m2

logger = logging.getLogger(__name__)

# 一行界址点： 序号 [点号 类别]  X(北)  Y(东)  …
#   临时用地表： 1 J1 1 3714615.396 40413266.562 喷涂 9.27
#   主体项目表： 4 3714132.075 40413212.886 0.43
_ROW = re.compile(
    r'^\s*(\d{1,3})\s+(?:[A-Za-z]{1,3}\d{1,4}\s+\d{1,2}\s+)?(\d{7}\.\d{2,})\s+(\d{8,}\.\d{2,})'
)


def _runs(text: str) -> List[List[Tuple[int, float, float]]]:
    """把文本切成一段段**连续的**坐标行（遇到非坐标行就断开）。"""
    out: List[List[Tuple[int, float, float]]] = []
    cur: List[Tuple[int, float, float]] = []
    for line in str(text or "").splitlines():
        m = _ROW.match(line)
        if m:
            cur.append((int(m.group(1)), float(m.group(2)), float(m.group(3))))
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _is_clean(run: Sequence[Tuple[int, float, float]]) -> bool:
    """段是否可信：≥3 点、序号严格递增、无连续重复点。见模块头「烂表整段弃用」。"""
    if len(run) < 3:
        return False
    seq = [r[0] for r in run]
    if any(b <= a for a, b in zip(seq, seq[1:])):
        return False
    pts = [(r[2], r[1]) for r in run]
    if any(a == b for a, b in zip(pts, pts[1:])):
        return False
    return True


def survey_polygons(text: str) -> List[dict]:
    """从材料文本里抽出所有**可信**的界址点多边形 → `[{points, area_m2, count}]`。

    `points` 为 `(东, 北)`：坐标表是「X=北, Y=东」，且东坐标带 40 带号。
    """
    out: List[dict] = []
    for run in _runs(text):
        if not _is_clean(run):
            continue
        pts = [(y - 40000000.0, x) for _no, x, y in run]
        a = polygon_area_m2(pts)
        if a <= 1.0:                       # 退化多边形（共线/重复）不算
            continue
        out.append({"points": pts, "area_m2": round(a, 2), "count": len(pts)})
    return out


def collect_survey_polygons(materials: Sequence[dict]) -> List[dict]:
    """跨材料收集，按 (点数, 面积) 去重，面积升序返回。"""
    seen = set()
    out: List[dict] = []
    for m in (materials or []):
        if not isinstance(m, dict):
            continue
        for poly in survey_polygons(m.get("text_content") or ""):
            key = (poly["count"], poly["area_m2"])
            if key in seen:
                continue
            seen.add(key)
            poly = dict(poly, source=str(m.get("source_name") or ""))
            out.append(poly)
    out.sort(key=lambda p: p["area_m2"])
    return out


# ═══════════════════════════════════════════════════════════════
# 绘图
# ═══════════════════════════════════════════════════════════════

_CN_FONTS = ("PingFang SC", "Heiti SC", "Songti SC", "Arial Unicode MS", "SimHei")


def _init_fonts() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    for f in _CN_FONTS:
        if f in have:
            plt.rcParams["font.sans-serif"] = [f]
            break
    plt.rcParams["axes.unicode_minus"] = False


def _north_arrow(ax) -> None:
    """指北针。🔴 必须用 **画布比例坐标** —— 用数据坐标会把范围撑爆，图成一条缝。"""
    ax.annotate("", xy=(0.93, 0.945), xytext=(0.93, 0.875),
                xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color="#333", lw=1.8))
    ax.text(0.93, 0.952, "北", ha="center", va="bottom", fontsize=11,
            color="#333", transform=ax.transAxes)


def _scale_bar(ax, meters: int = 50) -> None:
    """比例尺。🔴 长度按**真实米数**折算成画布比例，且须在数据范围定住之后再调。"""
    x0, x1 = ax.get_xlim()
    w = min(0.35, meters / max(1.0, (x1 - x0)))
    sx, sy = 0.04, -0.028
    ax.plot([sx, sx + w], [sy, sy], transform=ax.transAxes,
            color="#333", lw=3, solid_capstyle="butt", clip_on=False)
    ax.text(sx + w / 2, sy - 0.008, f"{meters} m", transform=ax.transAxes,
            ha="center", va="top", fontsize=9, color="#333")


def _draw_location_map(polygons: List[dict], out_png: Path, title: str) -> bool:
    """画「工程位置示意图」：最大的一块作底图范围，最小的一块＝复垦责任范围。

    面积与界址点数**由几何算出后写在图上**，不写死 —— 换了项目图会重算。

    🔴 画布取横版：装配器有一条「竖版地图转 90° 横放」的启发式（本意是摆正
       **竖版扫描件**）。自己的生成图若画成竖版，会被那条一并转躺 —— 连图题一起歪。
       所以这里以「宽 > 高」出图绕开它，注记改到画布右侧的空白处（figure 坐标）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    if not polygons:
        return False

    body, resp = polygons[-1], polygons[0]      # 最大=用地范围；最小=复垦责任范围
    fig = plt.figure(figsize=(10.0, 8.2))       # 🔴 宽 > 高：见上
    ax = fig.add_axes([0.04, 0.07, 0.50, 0.86])

    if body is not resp:
        ax.add_patch(MplPolygon(body["points"], closed=True, facecolor="#DCE6F1",
                                edgecolor="#2E75B6", lw=2, zorder=1))
    ax.add_patch(MplPolygon(resp["points"], closed=True, facecolor="#FDE9E9",
                            edgecolor="#C00000", lw=2.2, zorder=4))
    ax.plot([p[0] for p in resp["points"]], [p[1] for p in resp["points"]],
            "o", ms=2.2, color="#C00000", zorder=5)

    ax.autoscale_view()                          # 🔴 先定住数据范围，比例尺换算才对
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#999")

    # ── 注记面板：画布右侧空白处（figure 坐标），引线指到复垦范围中心 ──
    rx = sum(p[0] for p in resp["points"]) / len(resp["points"])
    ry = sum(p[1] for p in resp["points"]) / len(resp["points"])
    _lines = [("底图范围", f"{body['area_m2'] / 10000:.4f} 公顷")] if body is not resp else []
    _lines += [("复垦责任范围", f"{resp['area_m2']:.2f} ㎡"),
               ("", f"合 {resp['area_m2'] / 666.6667:.2f} 亩"),
               ("界址点", f"{resp['count']} 个")]
    ax.annotate("\n".join(f"{k}：{v}" if k else v for k, v in _lines),
                xy=(rx, ry), xycoords="data",
                xytext=(0.60, 0.52), textcoords=fig.transFigure,
                va="center", ha="left", fontsize=12, color="#C00000",
                bbox=dict(boxstyle="round,pad=0.5", fc="#FFF7F7", ec="#C00000", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#C00000", lw=1.4,
                                connectionstyle="arc3,rad=-0.15"), zorder=6)

    _north_arrow(ax)
    _scale_bar(ax, 50)
    fig.suptitle(title, fontsize=15, y=0.965)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    return True


def _draw_area_chart(filled: dict, out_png: Path, title: str) -> bool:
    """画「各类土地面积关系图」：按勘测定界地类出**横版柱状图**（第1章用）。

    数与第4章 表4-4 / 第1章 表1-1 同源（`land_classification`），不另算。
    样本里只有「污水处理厂」那份有这张图（图1-1），属可选图；取不到地类就不出。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lc = (filled or {}).get("land_classification") or {}
    rows = [r for r in (lc.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return False
    _labels, _areas = [], []
    for r in rows:
        try:
            _m2 = float(str(r.get("面积_m2") or 0).replace(",", ""))
        except Exception:
            _m2 = 0.0
        if _m2 <= 0:
            continue
        _n = f"{r.get('一级地类') or ''}" + (f"\n{r.get('二级地类')}" if r.get("二级地类") else "")
        _labels.append(_n)
        _areas.append(_m2 / 10000.0)
    if not _areas:
        return False

    _init_fonts()
    fig, ax = plt.subplots(figsize=(10.0, 5.6))       # 🔴 横版：宽 > 高
    bars = ax.bar(range(len(_areas)), _areas, color="#2E75B6", width=0.62)
    ax.set_xticks(range(len(_labels)))
    ax.set_xticklabels(_labels, fontsize=11)
    ax.set_ylabel("面积（公顷）", fontsize=11)
    ax.set_title(title, fontsize=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for b, v in zip(bars, _areas):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.4f}",
                ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, max(_areas) * 1.18)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    return True


# ═══════════════════════════════════════════════════════════════
# 对外入口
# ═══════════════════════════════════════════════════════════════

def ensure_reclamation_figures(materials: Sequence[dict],
                               storage_dir: str,
                               filled: Optional[dict] = None,
                               ) -> List[Dict[str, str]]:
    """从材料生成复垦正文配图 → `[{"path", "original_name"}]`（找不到几何就返回空表）。

    🔴 落盘位置与命名有讲究：图片目录解析相对路径时会回退到
       `storage/images/<图名>`（`image_catalog.build_image_catalog`），所以
       **文件名必须等于 `original_name`**，否则 `os.path.exists` 全不成立、图被丢弃。
       图名里带「位置示意图」是**绑定第3章的关键词**（见 `RECLAMATION_IMAGE_SPECS[3]`）。
    """
    out: List[Dict[str, str]] = []
    polys = collect_survey_polygons(materials)
    _init_fonts()

    # ① 第1章「各类土地面积关系图」（有地类数据就出，与界址点无关）
    _chart = "图1-1 各类土地面积关系图.jpg"
    try:
        if _draw_area_chart(filled or {}, Path(storage_dir) / "images" / _chart,
                            "各类土地面积关系图"):
            out.append({"path": f"images/{_chart}", "original_name": _chart})
            logger.info(f"[复垦图件] 已生成 {_chart}")
    except Exception as e:                       # pragma: no cover - 绘图失败不阻断
        logger.warning(f"[复垦图件] 面积关系图生成失败: {e}")

    if not polys:
        return out
    # 扩展名跟内容走：卫星底图出的是 JPEG；两条路径都用同一个名字，
    # 保证「位置示意图」这个**绑定第3章的关键词**在图名里（见模块头注释）。
    name = "图3-1 工程位置示意图.jpg"
    png = Path(storage_dir) / "images" / name

    # 🔴 首选**卫星影像底图 + 红线**（样本就是这么做出来的，见 `satellite_map` 模块头）。
    #    界址点可以确定性反算成经纬度，所以影像上的红线压得准，不用猜比例尺。
    #    取不到（没网/服务故障）就退回下面的几何示意图 —— **绝不阻断出报告**。
    _ok = False
    try:
        from app.services.satellite_map import render_location_figure
        _ok = render_location_figure(
            polys[0]["points"], str(png),
            cache_dir=str(Path(storage_dir) / "satellite_tiles"),
            title="工程位置示意图")
    except Exception as e:                       # pragma: no cover
        logger.warning(f"[复垦图件] 卫星底图出图失败，退回几何示意图: {e}")
        _ok = False

    if not _ok:
        try:
            if not _draw_location_map(polys, png, "工程位置示意图"):
                return out
            logger.info("[复垦图件] 卫星底图不可用，已退回几何示意图")
        except Exception as e:                   # pragma: no cover - 绘图失败不阻断出报告
            logger.warning(f"[复垦图件] 位置示意图生成失败: {e}")
            return out

    logger.info(f"[复垦图件] 已生成 {name}（{len(polys)} 个界址点多边形，"
                f"复垦范围 {polys[0]['area_m2']:.2f} ㎡）")
    return out + [{"path": f"images/{name}", "original_name": name}]
