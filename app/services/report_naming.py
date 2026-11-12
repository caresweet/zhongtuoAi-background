# -*- coding: utf-8 -*-
"""报告文件名 —— 每次新生成都带**时间戳标识**，不覆盖上一版。

业主 2026-11-12（新口径，替代 11-03 的「固定文件名覆盖」）：
    「每次新生成一个报告之后重新创建报告名称加个标识」
原因：Downloads 里同时躺着 `测试_城东污水厂_复垦方案.docx`（新）和
`测试_城东污水厂_复垦方案 - 11.12.docx`（旧的专家批注稿），打开时点错，
看到的还是旧批注。

规则：
    `测试_城东污水厂_复垦方案.docx` → `测试_城东污水厂_复垦方案_20261112_1530.docx`
    · 已带 `_YYYYMMDD_HHMM` 的不重复追加（幂等）；
    · 旧文件保留，靠名字区分新旧 —— 不再覆盖、不再靠「- 11.12」这种人工后缀。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

_TS = re.compile(r'_\d{8}_\d{4}$')


def stamped_filename(name: str, when: Optional[datetime] = None) -> str:
    """给报告文件名加 `_YYYYMMDD_HHMM` 后缀（已有则不重复加）。"""
    base = os.path.basename(str(name or "").strip())
    stem, ext = os.path.splitext(base)
    if not stem:
        stem, ext = "报告", ".docx"
    if _TS.search(stem):
        return f"{stem}{ext}"
    ts = (when or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"{stem}_{ts}{ext}"
