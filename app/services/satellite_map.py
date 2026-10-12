# -*- coding: utf-8 -*-
"""图3-1「工程位置示意图」—— **卫星影像底图 + 红线**。

## 为什么这么做

样本（明远路报告书 p14）的图3-1 是**卫星影像**，上面手工描了红色边界，
还带着「明远路」「通甫路学校」「黄码北干渠」「王庄」这类地名 —— 是编制人员
自己截的影像图。我们此前出的是**纯几何轮廓**（白底 + 一条红线），
业主一眼看出「图片不对」。

## 为什么不用勘测定界图当底图

先试过：材料里的「勘测定界图」（1:2000 彩色地类图）**没有坐标格网注记**
（图廓内只有地类界），左侧边缘也无数值 —— 没有控制点，**无法配准**；
用视觉读散落的 J 标签像素位置也不可靠（实测模型只返回 3 个点、x 还全一样）。

## 坐标怎么落到影像上（确定性，不猜比例尺）

勘测定界的界址点是 **CGCS2000 高斯-克吕格 3 度带 40 带**（中央子午线 120°）。
本模块自带**横轴墨卡托反算**（Snyder 级数，不引入 pyproj），把 (北, 东) 反算成
经纬度；再按 Web Mercator 落到卫星瓦片上。实测明远佳苑临时用地反算出
119.0660°E / 33.5543°N —— 正在淮安经济技术开发区；影像上红线**正好压在
那条彩钢瓦临时设施带上**，与样本同一处。

底图来源：Esri World Imagery（XYZ 瓦片，免密钥）。业主口径（见
`reclamation_image_filler` 的模块注释）：「报审文件用网络图片存在版权风险，
地图类另涉及审图号 —— 业主已知悉并接受」。取不到就**退回几何示意图**，不报错。
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# CGCS2000 ≈ WGS84 椭球
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)

# Esri World Imagery：`{z}/{y}/{x}`（注意行列顺序与常见的 {z}/{x}/{y} 相反）
_TILE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "World_Imagery/MapServer/tile/{z}/{y}/{x}")
_TILE_SIZE = 256
_DEFAULT_ZOOM = 18          # ≈0.5 m/px（纬度 33.5°），足够看清建筑轮廓
_FETCH_TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════
# 坐标换算
# ═══════════════════════════════════════════════════════════════

def tm_inverse(east: float, north: float, lon0_deg: float = 120.0) -> Tuple[float, float]:
    """高斯-克吕格（横轴墨卡托）反算 → `(lat, lon)` 度。

    `east` 要**先去掉带号与 500000 假东**（如 Y=40413266.562 → 413266.562 → -86733.438）。
    公式取 Snyder《Map Projections — A Working Manual》的级数解，精度对 3° 带足够。
    """
    e = math.sqrt(_E2)
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    mu = north / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = _E2 / (1 - _E2) * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = east / n1
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _E2 / (1 - _E2)) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _E2 / (1 - _E2)
           - 3 * c1 ** 2) * d ** 6 / 720)
    lon = lon0_deg + math.degrees((
        d - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _E2 / (1 - _E2)
           + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(phi1))
    return math.degrees(lat), lon


def zone_of(east_with_zone: float) -> Tuple[int, float]:
    """带 40 带号的东坐标 → `(带号, 去掉带号与假东的东坐标)`。

    实测值形如 `40413266.562`（40 带 + 500000 假东）；`413266.562` 这种
    已去带号的也认。
    """
    v = float(east_with_zone)
    if v >= 10_000_000:                      # 带 6 位带号前缀
        z = int(v // 1_000_000)
        v = v - z * 1_000_000
    else:
        z = 40
    return z, v - 500000.0


def points_to_lonlat(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """`[(东, 北)]`（勘测定界的原始口径，东坐标含 40 带号）→ `[(lon, lat)]`。"""
    out = []
    for east, north in points:
        z, e = zone_of(east)
        lat, lon = tm_inverse(e, float(north), lon0_deg=z * 3.0)
        out.append((lon, lat))
    return out


def _to_tile(lon: float, lat: float, z: int) -> Tuple[float, float]:
    """经纬度 → Web Mercator 瓦片坐标（浮点，便于算像素）。"""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def meters_per_pixel(lat: float, z: int) -> float:
    """该纬度、该缩放级别下每像素的米数（像元分辨率）。"""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


# ═══════════════════════════════════════════════════════════════
# 瓦片抓取
# ═══════════════════════════════════════════════════════════════

def _tile(z: int, x: int, y: int, cache_dir: Path):
    """取一张瓦片（PIL Image）；磁盘缓存命中就不联网。失败返回 None。"""
    from PIL import Image
    f = cache_dir / str(z) / str(x) / f"{y}.jpg"
    if f.exists() and f.stat().st_size > 500:
        try:
            return Image.open(f).convert("RGB")
        except Exception:
            pass
    try:
        with urllib.request.urlopen(
                _TILE_URL.format(z=z, x=x, y=y), timeout=_FETCH_TIMEOUT) as r:
            raw = r.read()
        if len(raw) < 500:
            return None
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(raw)
        except Exception:
            pass
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        logger.warning(f"[卫星底图] 瓦片 {z}/{x}/{y} 取不到: {e}")
        return None


def fetch_basemap(lonlat: Sequence[Tuple[float, float]], cache_dir: str,
                  zoom: int = _DEFAULT_ZOOM, pad_deg: float = 0.0011):
    """按经纬度范围抓取并拼接卫星底图 → `(PIL Image, tx0, ty0, zoom)`；失败返回 None。

    `tx0/ty0` 是拼接图左上角对应的瓦片号，用来把经纬度换算成**图像像素**。
    """
    from PIL import Image
    if not lonlat:
        return None
    lons = [p[0] for p in lonlat]
    lats = [p[1] for p in lonlat]
    x0, y0 = _to_tile(min(lons) - pad_deg, max(lats) + pad_deg, zoom)
    x1, y1 = _to_tile(max(lons) + pad_deg, min(lats) - pad_deg, zoom)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 200:     # 安全阀：别把服务打爆
        logger.warning("[卫星底图] 范围过大会抓 %d 张瓦片，放弃",
                       (tx1 - tx0 + 1) * (ty1 - ty0 + 1))
        return None
    cd = Path(cache_dir)
    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * _TILE_SIZE, (ty1 - ty0 + 1) * _TILE_SIZE),
                       (240, 240, 240))
    got = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            im = _tile(zoom, tx, ty, cd)
            if im is None:
                continue
            canvas.paste(im, ((tx - tx0) * _TILE_SIZE, (ty - ty0) * _TILE_SIZE))
            got += 1
    if got == 0:
        return None
    if got < (tx1 - tx0 + 1) * (ty1 - ty0 + 1) * 0.6:
        logger.warning(f"[卫星底图] 只取到 {got} 张瓦片，底图不完整，放弃")
        return None
    return canvas, tx0, ty0, zoom


def _px(lon: float, lat: float, tx0: int, ty0: int, z: int) -> Tuple[float, float]:
    x, y = _to_tile(lon, lat, z)
    return (x - tx0) * _TILE_SIZE, (y - ty0) * _TILE_SIZE


# ═══════════════════════════════════════════════════════════════
# 出图
# ═══════════════════════════════════════════════════════════════

_CN_FONTS = ("PingFang SC", "Heiti SC", "Songti SC", "Arial Unicode MS", "STHeiti", "SimHei")
# 兜底路径（前两条是本机实测存在的；上面先用 matplotlib 的字体索引找，更稳）
_CN_FONT_PATHS = (
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",      # Linux（容器里）
    "C:/Windows/Fonts/simhei.ttf",                        # Windows
)


def _cjk_font_path() -> Optional[str]:
    """找一个能画中文的字体文件路径。

    🔴 先问 **matplotlib 的字体索引** —— 它已经把系统字体（含 macOS 那些
       `AssetsV2/.../*.asset` 目录里的）都扫过一遍，比手工拼路径可靠：
       实测手工拼 `/System/Library/Fonts/PingFang SC.ttc` 是不存在的，
       结果整张图的中文全成了方框。
    """
    try:
        from matplotlib import font_manager
        _have = {f.name: f.fname for f in font_manager.fontManager.ttflist}
        for n in _CN_FONTS:
            p = _have.get(n)
            if p and os.path.exists(p):
                return p
    except Exception:
        pass
    return next((p for p in _CN_FONT_PATHS if os.path.exists(p)), None)


_FONT_CACHE: Dict[int, object] = {}


def _font(size: int):
    from PIL import ImageFont
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    f = None
    p = _cjk_font_path()
    if p:
        try:
            f = ImageFont.truetype(p, size)
        except Exception:
            f = None
    if f is None:
        logger.warning("[卫星底图] 找不到中文字体，图上中文会显示为方框")
        f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def render_location_figure(points: Sequence[Tuple[float, float]], out_path: str,
                           cache_dir: str, title: str = "工程位置示意图") -> bool:
    """出「工程位置示意图」：卫星影像 + 红线 + 比例尺 + 指北针 + 面积标注。

    `points` 是勘测定界的 `[(东, 北)]`（东坐标含 40 带号）。成功返回 True。
    """
    from PIL import Image, ImageDraw
    lonlat = points_to_lonlat(points)
    if len(lonlat) < 3:
        return False
    got = fetch_basemap(lonlat, cache_dir)
    if got is None:
        return False
    canvas, tx0, ty0, z = got
    canvas = canvas.convert("RGB")
    px = [_px(lo, la, tx0, ty0, z) for lo, la in lonlat]

    # ── 裁剪到「边界 + 留白」，让地标（学校/干渠）进画面 ──
    xs = [p[0] for p in px]; ys = [p[1] for p in px]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    m = int(max(60, span * 0.45))
    box = (max(0, int(min(xs)) - m), max(0, int(min(ys)) - m),
           min(canvas.width, int(max(xs)) + m), min(canvas.height, int(max(ys)) + m))
    img = canvas.crop(box)
    px = [(x - box[0], y - box[1]) for x, y in px]

    dr = ImageDraw.Draw(img)
    dr.line(px + [px[0]], fill=(230, 0, 0), width=4, joint="curve")
    for x, y in px:
        dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(230, 0, 0))
    # 复垦责任范围注记
    cx = sum(p[0] for p in px) / len(px); cy = sum(p[1] for p in px) / len(px)
    f_big, f_sm = _font(26), _font(19)
    _area = _polygon_area(points)
    lines = [f"复垦责任范围 {_area:.2f} ㎡",
             f"合 {_area / 666.6667:.2f} 亩    界址点 {len(points)} 个"]
    for i, s in enumerate(lines):
        dr.text((cx + 40, cy - 20 + i * 30), s, fill=(255, 255, 255), font=f_sm,
                stroke_width=3, stroke_fill=(120, 0, 0))
    # 比例尺（按像元分辨率换算，不猜）
    mpp = meters_per_pixel(lonlat[0][1], z)
    bar_m = 50.0
    bar_px = int(bar_m / mpp)
    bx, by = 24, img.height - 46
    dr.rectangle([bx - 6, by - 26, bx + bar_px + 6, by + 26], fill=(255, 255, 255))
    dr.line([bx, by, bx + bar_px, by], fill=(0, 0, 0), width=4)
    dr.text((bx, by + 4), f"{bar_m:.0f} m", fill=(0, 0, 0), font=f_sm)
    # 指北针
    ax, ay = img.width - 60, 40
    dr.rectangle([ax - 26, ay - 26, ax + 26, ay + 62], fill=(255, 255, 255))
    dr.line([ax, ay + 50, ax, ay + 6], fill=(0, 0, 0), width=4)
    dr.polygon([(ax, ay - 6), (ax - 9, ay + 12), (ax + 9, ay + 12)], fill=(0, 0, 0))
    dr.text((ax - 8, ay + 26), "北", fill=(0, 0, 0), font=f_sm)
    # 图题 + 底图来源
    dr.rectangle([0, 0, img.width, 44], fill=(255, 255, 255))
    dr.text((14, 9), title, fill=(0, 0, 0), font=f_big)
    dr.text((14, img.height - 22), "底图：Esri World Imagery（卫星影像）",
            fill=(255, 255, 255), font=_font(15), stroke_width=2, stroke_fill=(60, 60, 60))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    # 🔴 存 JPEG 不存 PNG：这类卫星影像 PNG 要 2.3MB，JPEG(85) 只要 ~400KB，
    #    而且**扩展名与内容一致**（既有管线里有过「.png 里装 JPEG」的坑）。
    img.convert("RGB").save(out_path, "JPEG", quality=85, optimize=True)
    logger.info(f"[图3-1] 卫星底图 + 红线出图完成 {img.size} → {out_path}")
    return True


def _polygon_area(points: Sequence[Tuple[float, float]]) -> float:
    """鞋带公式（与 `boundary_points.polygon_area_m2` 同口径，避免循环依赖）。"""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0
