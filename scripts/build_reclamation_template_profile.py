# -*- coding: utf-8 -*-
"""为一份复垦地区模板生成「逐节三色画像」。

用法：
    cd backend
    python -m scripts.build_reclamation_template_profile --template-id 39
    python -m scripts.build_reclamation_template_profile --path storage/templates/xxx.docx

产物：data/template_profiles/reclamation_<文件名>.json
生成报告时，第 N 章的 prompt 会自动读取并注入本章画像。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.reclamation_template_profile import (
    build_profile_from_docx, save_profile,
)


async def _by_id(template_id: int):
    from sqlalchemy import select
    from app.database.knowledge_db import async_session
    from app.models.knowledge import Template

    async with async_session() as db:
        t = (await db.execute(
            select(Template).where(Template.id == template_id))).scalars().first()
    if not t:
        raise SystemExit(f"模板 {template_id} 不存在")
    from app.services.file_service import file_service
    p = Path(str(file_service.get_absolute_path(t.template_file_path)))
    if not p.exists():
        raise SystemExit(f"模板文件不存在：{p}")
    return t, p


def _summary(prof: dict) -> None:
    print("章  节数  固定段  换数段  创作段")
    for no in sorted(prof["chapters"], key=lambda x: int(x) if x.isdigit() else 99):
        secs = prof["chapters"][no]["sections"]
        f = sum(len(s["fixed"]) for s in secs)
        sl = sum(len(s["slot"]) for s in secs)
        c = sum(len(s["create"]) for s in secs)
        print(f"{no:>2}  {len(secs):>4}  {f:>5}  {sl:>5}  {c:>5}   {prof['chapters'][no].get('title','')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template-id", type=int, default=0)
    ap.add_argument("--path", default="")
    args = ap.parse_args()

    async def run():
        meta = {}
        if args.template_id:
            t, p = await _by_id(args.template_id)
            meta = {"template_id": t.id, "name": t.name,
                    "region": t.region or "", "domain": t.domain}
        elif args.path:
            p = Path(args.path).expanduser()
            if not p.exists():
                raise SystemExit(f"文件不存在：{p}")
        else:
            raise SystemExit("需要 --template-id 或 --path")
        prof = build_profile_from_docx(p)
        prof.update(meta)
        out = save_profile(prof)
        print(f"已生成：{out}")
        _summary(prof)

    asyncio.run(run())


if __name__ == "__main__":
    main()
