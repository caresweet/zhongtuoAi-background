# -*- coding: utf-8 -*-
"""CAD 图纸（DXF / DWG）→ PNG 渲染。

**为什么需要**：土地复垦材料的「临时用地功能分布图」「主体项目红线图」，
业主给的就是 CAD 图纸 —— 实测 `明远佳苑` 材料包里是 AutoCAD 2004 的 `.dwg`（文件头
`AC1018`）加一份 ASCII `.dxf`。而报告里要的是**图片**，所以上传后得先渲成 PNG，
才能进既有的图片分类 / 放置流水线。

| 格式 | 处理 |
|---|---|
| `.dxf` | 直接渲染（`ezdxf` + `matplotlib`，纯 Python 依赖） |
| `.dwg` | `ezdxf` **读不了**（专有格式）。装了 `dwgread`（libredwg，`brew install libredwg`）则先转 DXF 再渲染；没装**也能上传留存**，只是渲不出图 —— 此时返回明确原因，不静默失败。 |

渲染失败**不抛异常给上传接口**：图纸仍然存下来（用户能下回来、也能作为附件原样附上），
只是拿不到预览图。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CAD_EXTENSIONS = (".dxf", ".dwg")
_DWGREAD = "dwgread"
_RENDER_TIMEOUT = 120


def _dwg_to_dxf(src: str) -> Optional[str]:
    """`.dwg` → 临时 `.dxf`（缺 `dwgread` 时返回 None）。"""
    if shutil.which(_DWGREAD) is None:
        return None
    out = os.path.join(tempfile.mkdtemp(prefix="dwg2dxf_"), "out.dxf")
    try:
        subprocess.run([_DWGREAD, "-O", "DXF", "-o", out, src],
                       check=True, timeout=_RENDER_TIMEOUT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        logger.warning(f"[CAD] dwgread 转换失败 {os.path.basename(src)}: {e}")
        return None
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def render_cad_to_png(src: str, out_png: str, *, dpi: int = 200,
                      background: str = "#FFFFFF") -> Tuple[bool, str]:
    """把一张 CAD 图纸渲成 PNG。

    Returns:
        (是否成功, 说明)。**不抛异常** —— 上传流程不该因为一张图纸渲不出来就整单失败。
    """
    p = str(src or "")
    if not p or not os.path.exists(p):
        return False, "文件不存在"
    ext = Path(p).suffix.lower()
    if ext not in CAD_EXTENSIONS:
        return False, f"不是 CAD 图纸: {ext}"

    dxf_path = p
    if ext == ".dwg":
        converted = _dwg_to_dxf(p)
        if converted is None:
            return False, ("DWG 需要 dwgread 才能渲染（brew install libredwg）；"
                           "也可以在 CAD 里另存为 DXF 后重传")
        dxf_path = converted

    try:
        import matplotlib
        matplotlib.use("Agg")           # 无界面后端，服务端渲染
        import matplotlib.pyplot as plt
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except Exception as e:              # 依赖缺失
        return False, f"渲染依赖缺失: {type(e).__name__}: {e}"

    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        if len(msp) == 0:
            return False, "图纸里没有可渲染的实体"

        fig = plt.figure(figsize=(11.69, 8.27))     # A4 横向
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_axis_off()
        fig.patch.set_facecolor(background)
        ax.set_facecolor(background)
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(
            msp, finalize=True)
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, dpi=dpi, facecolor=background)
        plt.close(fig)
        if not os.path.exists(out_png) or os.path.getsize(out_png) < 1024:
            return False, "渲染结果为空"
        return True, f"已渲染 {os.path.getsize(out_png) // 1024} KB"
    except Exception as e:
        logger.warning(f"[CAD] 渲染失败 {os.path.basename(p)}: {e}")
        return False, f"渲染失败: {type(e).__name__}: {e}"
