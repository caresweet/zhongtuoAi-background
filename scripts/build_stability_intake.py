# -*- coding: utf-8 -*-
"""生成《稳评数据资料表》Excel 模板。

用法：
    cd backend
    python -m scripts.build_stability_intake

同时写两份：
    storage/templates/稳评数据资料表_模板.xlsx   （系统内分发）
    ~/Downloads/稳评数据资料表_模板.xlsx        （本地直接用）
"""
from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> None:
    import sys
    sys.path.insert(0, str(BACKEND))
    from app.services.stability_intake import build_intake_workbook

    targets = [BACKEND / "storage" / "templates" / "稳评数据资料表_模板.xlsx",
               Path.home() / "Downloads" / "稳评数据资料表_模板.xlsx"]
    for p in targets:
        build_intake_workbook(p)
        print(p)


if __name__ == "__main__":
    main()
