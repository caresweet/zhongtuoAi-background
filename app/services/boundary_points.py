# -*- coding: utf-8 -*-
"""界址点坐标 → 用地面积（**确定性**，不依赖 OCR / LLM）。

## 为什么要有它

业主口径「面积以勘测定界为准」。但实测里面积被抽错过：文本正则把
环保批文里的「总建筑面积 661303 ㎡」当成了用地面积。

而勘测定界同时提供**界址点坐标**（`*.dat` 文本，或 CAD 里的闭合多段线）——
从坐标算面积是**几何确定性**的，不受文本抽取影响。实测验证：

    明远佳苑 42 个界址点 → 8068.88 ㎡
    勘测定界报告写的      → 8070.00 ㎡     （差 0.014%，吻合）

## `.dat` 格式

南方 CASS / 勘测定界常用格式，每行一个点：

    <点号>  <编码>  <Y 东坐标>  <X 北坐标>   [高程]

点号/编码常是 GBK 中文，坐标是小数。本模块**只认坐标**，
按「一行里取最后两个（或三个）浮点数」的宽松规则解析，兼容常见的几种排法。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

_NUM = re.compile(r'-?\d+\.\d+')


def parse_boundary_points(text: str) -> List[Tuple[float, float]]:
    """从文本里取界址点坐标 → [(x, y), ...]（按行序，去重保序）。

    每行取**前两个**带小数点的数作为 (x, y)。勘测定界的 `.dat` 里
    通常是「点号 编码 Y X」，点号可能是中文、也可能被 GBK 解成乱码 ——
    所以不按列位切，只挑数值。
    """
    pts: List[Tuple[float, float]] = []
    for line in str(text or "").splitlines():
        nums = _NUM.findall(line)
        if len(nums) < 2:
            continue
        x, y = float(nums[0]), float(nums[1])
        if not pts or (x, y) != pts[-1]:        # 末点常与首点重复，先不去掉
            pts.append((x, y))
    # 闭合环：首尾相同时去掉末点
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def polygon_area_m2(points: List[Tuple[float, float]]) -> float:
    """鞋带公式（高斯面积），返回平方米（取绝对值）。"""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def read_boundary_file(path: str) -> dict:
    """读一个界址点文件 → {points, count, area_m2, area_ha, area_mu}。

    文件不存在 / 解不出点 → 各字段给 0 / 空，**不抛异常**（调用方按「没解析到」处理）。
    """
    out = {"points": [], "count": 0, "area_m2": 0.0, "area_ha": 0.0, "area_mu": 0.0}
    p = str(path or "")
    if not p or not Path(p).exists():
        return out
    raw = Path(p).read_bytes()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:                                   # pragma: no cover - 三种编码都失败极罕见
        return out

    pts = parse_boundary_points(text)
    if len(pts) < 3:
        return out
    a = polygon_area_m2(pts)
    out.update(points=pts, count=len(pts), area_m2=round(a, 2),
               area_ha=round(a / 10000.0, 4), area_mu=round(a / 666.6667, 2))
    return out


def first_boundary_file(paths) -> Optional[str]:
    """从一串路径里挑出最像界址点数据的那个（`.dat` 优先，其次含「界址/坐标」的 txt）。"""
    cands = [str(p) for p in (paths or []) if p]
    for suf in (".dat",):
        for p in cands:
            if p.lower().endswith(suf):
                return p
    for p in cands:
        low = p.lower()
        if low.endswith((".txt", ".csv")) and any(k in p for k in ("界址", "坐标", "点")):
            return p
    return None
