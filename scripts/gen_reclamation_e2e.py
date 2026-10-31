#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端跑一份**复垦**报告：把本地材料目录当作「用户上传」→ 走工作流 → 出 docx。

用途：业主拿来验证「不管前端、只看产物」的自动化测试台。
    cd backend
    .venv/bin/python scripts/gen_reclamation_e2e.py --dir ~/Downloads/井神资料

与界面口径的差异（刻意为之）：
  · ENABLE_DATA_INQUIRY / ENABLE_GAP_INQUIRY 关闭 —— 脚本没人回答弹窗；
  · 会话归属固定 admin(1)/江苏众拓(1)，保证公司简介、资质、人员证书能取到；
  · 上传逻辑**照抄上传路由**（CAD/Shapefile 出图那两段也照搬），否则图和界面不一致。

产物：storage/generated/*.docx + storage/output/e2e_ch{N}.md（便于逐章排查）
"""

import argparse
import asyncio
import io
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("ENABLE_DATA_INQUIRY", "false")
os.environ.setdefault("ENABLE_GAP_INQUIRY", "false")

_SKIP_EXT = {".zip", ".rar", ".ds_store"}


async def _save_and_collect(root: Path) -> list:
    import logging
    from fastapi import UploadFile
    from starlette.datastructures import Headers
    from app.services.file_service import FileService
    from app.config import settings

    log = logging.getLogger("e2e")
    uploaded = []
    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and not p.name.startswith("._")
             and p.name != ".DS_Store" and p.suffix.lower() not in _SKIP_EXT]
    log.info(f"[E2E] 待上传 {len(files)} 个文件")
    for p in files:
        rel = str(p.relative_to(root))
        try:
            with open(p, "rb") as f:
                data = f.read()
            uf = UploadFile(file=io.BytesIO(data), filename=rel,
                            headers=Headers({"content-type": "application/octet-stream"}))
            r = await FileService.save_attachment(uf)
        except Exception as e:
            log.warning(f"[E2E] 跳过 {rel}: {e}")
            continue
        uploaded.append({"path": r["relative_path"],
                         "original_name": r.get("original_name") or rel,
                         "file_type": r.get("file_type", "file")})

        # ── CAD / Shapefile 出图（与 routers/report.py 的 upload 分支一致）──
        _ext = Path(r["relative_path"]).suffix.lower()
        try:
            if _ext in (".dwg", ".dxf"):
                from app.services.cad_renderer import render_cad_to_png
                _png = str(Path(r["relative_path"]).with_suffix(".cad.png"))
                ok, why = render_cad_to_png(str(Path(settings.STORAGE_DIR) / r["relative_path"]),
                                            str(Path(settings.STORAGE_DIR) / _png))
                if ok:
                    uploaded.append({"path": _png, "original_name": Path(rel).stem + "_图纸.png",
                                     "file_type": "image"})
                log.info(f"[E2E] CAD {rel}: {why}")
            elif _ext == ".shp":
                from app.services.shapefile_renderer import render_shapefile_to_png
                _png = str(Path(r["relative_path"]).with_suffix(".shp.png"))
                ok, why = render_shapefile_to_png(str(Path(settings.STORAGE_DIR) / r["relative_path"]),
                                                  str(Path(settings.STORAGE_DIR) / _png))
                if ok:
                    uploaded.append({"path": _png, "original_name": Path(rel).stem + "_矢量图.png",
                                     "file_type": "image"})
                log.info(f"[E2E] SHP {rel}: {why}")
        except Exception as e:
            log.warning(f"[E2E] 出图跳过 {rel}: {e}")
    return uploaded


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="材料目录")
    ap.add_argument("--title", default="盐业井神张兴矿区ZX9、ZX10井施工便道临时用地土地复垦方案")
    args = ap.parse_args()

    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"目录不存在: {root}")

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    from app.services.report_service import report_service
    from app.agent.state import create_initial_state

    sess = report_service.create_session(
        template_id=0, template_name="内置复垦方案标准结构（知识库综合生成）",
        template_path="", initial_message=args.title, user_id="1", company_id=1)
    state = sess.state
    state.update(create_initial_state(
        session_id=sess.session_id, report_title=args.title,
        project_context=args.title, domain="reclamation"))
    state["_domain"] = state["domain"] = state["_conversation_domain"] = "reclamation"
    state["_company_id"] = 1

    t0 = time.time()
    state["_uploaded_files"] = await _save_and_collect(root)
    print(f"[E2E] 上传完成 {len(state['_uploaded_files'])} 项，用时 {time.time()-t0:.0f}s")
    report_service._save_sessions()

    from app.services.report_workflow import workflow_runner
    wf = await workflow_runner.start(
        session_id=sess.session_id, report_title=args.title,
        materials_dir="", project_context=args.title, existing_state=state)

    intr = (wf or {}).get("_interrupt")
    print("[E2E] interrupt:", "YES" if intr else "no", intr if intr else "")
    out = (wf or {}).get("output_path") or state.get("output_path")
    print("[E2E] output:", out)
    # 章节 markdown 落盘，便于逐章核对
    try:
        od = BACKEND / "storage" / "output"
        od.mkdir(parents=True, exist_ok=True)
        for k, v in ((wf or {}).get("chapters") or state.get("chapters") or {}).items():
            md = (v or {}).get("markdown") if isinstance(v, dict) else ""
            if md:
                (od / f"e2e_ch{k}.md").write_text(md, encoding="utf-8")
        print("[E2E] 章节 markdown →", od)
    except Exception as e:
        print("[E2E] 章节落盘失败:", e)
    print(f"[E2E] 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
