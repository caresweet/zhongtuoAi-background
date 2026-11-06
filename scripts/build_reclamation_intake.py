# -*- coding: utf-8 -*-
"""生成《复垦基础资料》Excel 模板。

用法：
    cd backend
    python -m scripts.build_reclamation_intake
    python -m scripts.build_reclamation_intake --out ~/Downloads/复垦基础资料_模板.xlsx

默认同时写两份：
    storage/templates/复垦基础资料_模板.xlsx   （系统内下载/分发）
    ~/Downloads/复垦基础资料_模板.xlsx          （本地直接用）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.services.reclamation_intake import build_intake_workbook


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="额外输出路径（可重复用逗号分隔）")
    args = ap.parse_args()

    backend = Path(__file__).resolve().parents[1]
    targets = [backend / "storage" / "templates" / "复垦基础资料_模板.xlsx",
               Path.home() / "Downloads" / "复垦基础资料_模板.xlsx"]
    for extra in (args.out or "").split(","):
        if extra.strip():
            targets.append(Path(extra.strip()).expanduser())

    for p in targets:
        build_intake_workbook(p)
        print(p)


if __name__ == "__main__":
    main()
