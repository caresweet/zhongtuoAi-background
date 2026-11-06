# -*- coding: utf-8 -*-
"""生成一份复垦测试报告（用井神测试数据 + 材料里的设计参数）。

用法（在 backend 容器内）：
    python scripts/generate_reclamation_test.py

流程与正式生成一致：先 `fix_reclamation_fields`（材料优先取设计参数）→
`enrich_filled_data`（工程量/投资）→ 逐章 Agent → 装配 DOCX。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

SESSION_ID = "test_recl_20261103"

# 井神三井组测试数据（地类按人工稿 11.2 口径）
TEST_FILLED = {
    "project_name": "江苏盐业井神楚州张兴储气库工程三井组临时用地",
    "project_title": "江苏盐业井神楚州张兴储气库工程三井组临时用地土地复垦方案",
    "project_unit": "江苏苏盐井神股份有限公司第一分公司",
    "build_unit": "江苏苏盐井神股份有限公司第一分公司",
    "unit_address": "江苏省淮安市淮安区井神路1号",
    "location": "淮安市淮安区山阳街道黄土桥村",
    "area_m2": "23248",
    "land_use_start": "2026年10月1日",
    "land_use_end": "2028年9月30日",
    "land_use_months": "24",
    "land_use_period": "2026年10月1日至2028年9月30日",
    "doc_reference": "苏自然资规〔2026〕1号",
    "legal_rep": "沙宝宏",
    "contact_phone": "13770401447",
    "unit_nature": "有限公司",
    "qualification_authority": "江苏省土地学会",
    "qualification_level": "一级",
    "company_contact": "刘利伟",
    "company_phone": "13770380120",
    "land_classification": {
        "合计_m2": 23248.0,
        "rows": [
            {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 22215.0},
            {"一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 417.0},
            {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 616.0},
        ],
    },
    "land_use_zones": ("井场 0.2310公顷 挖损和压占 重度 水田；"
                       "施工场站 1.1950公顷 压占 重度 水田、农村道路、沟渠；"
                       "施工便道 0.5788公顷 压占 重度 水田、农村道路、沟渠；"
                       "表土堆放区 0.3200公顷 压占 中度 水田"),
    "_selected_personnel": ["陈春", "林叶青", "刘利伟"],
}

# 模拟用户上传的「井神复垦方案/施工组织设计」文本：设计参数只在这里，不在 filled 里
DESIGN_MATERIAL = """
江苏盐业井神楚州张兴储气库工程三井组临时用地土地复垦方案
项目区面积8.0762hm2，临时用地面积2.3248hm2，共36个地块。
江苏盐业井神楚州张兴储气库工程总投资额为651003.92万元。
项目性质：新建工程。
项目临时用地使用时间为24个月，从2026年10月至2028年9月。
拟安排在使用结束后6个月内完成所有临时用地的复垦和验收工作，之后考虑6个月的管护期。
最终确定土地复垦方案服务年限为36个月，从2026年10月到2029年9月。
临时用地施工前，表土剥离主要针对现状的耕地，剥离表层30cm，共计剥离表土6664.5m³。
场地采用15cm厚的碎石垫层，上面铺钢板，钢板由建设单位收回。
项目区需清除地面垫层的面积为2.0048hm2。
"""


def _load_prev_images() -> tuple[list, dict]:
    """复用上一次测试会话的井神图件（现状图/勘界图/调查表）。"""
    db = Path("data/history_reports.db")
    if not db.exists():
        return [], {}
    try:
        c = sqlite3.connect(str(db))
        row = c.execute(
            "select filled_data_json from reports where id=97").fetchone()
        if not row:
            return [], {}
        fd = json.loads(row[0])
        imgs = [p for p in (fd.get("_extracted_images") or [])
                if any(k in p for k in ("现状图", "勘界图", "调查表"))]
        meta = {k: fd.get(k) for k in ("_centroid_lat", "_centroid_lon") if fd.get(k)}
        return imgs[:120], meta
    except Exception:
        return [], {}


def _load_human112_images() -> tuple[list, list]:
    """从人工稿 11.2 里抽出图件，按图题命名，供测试会话当上传图片用。

    测试 checkpoint 被清后，原脚本取不到井神现场图；这份人工稿本身就是同一项目，
    用它的图做绑定测试是合适的（别项目绝不能用）。
    """
    import hashlib
    import shutil
    import zipfile
    from docx import Document
    from docx.oxml.ns import qn
    from app.config import settings

    src = Path.home() / "Downloads" / "1井神安井组复垦报告11.2.docx"
    if not src.exists():
        return [], []
    outdir = Path(settings.STORAGE_DIR) / "images" / "human112"
    outdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        media = {n: z.read(n) for n in z.namelist() if n.startswith("word/media/")}
    d = Document(str(src))
    paras = d.paragraphs
    rels = d.part.rels
    items, paths = [], []
    used = set()
    for i, p in enumerate(paras):
        blips = p._p.findall(".//" + qn("a:blip"))
        if not blips:
            continue
        caption = p.text.strip()
        if not caption.startswith("图"):
            for j in list(range(i - 2, i)) + list(range(i + 1, i + 3)):
                if 0 <= j < len(paras):
                    t = paras[j].text.strip()
                    if t.startswith("图"):
                        caption = t
                        break
        for k, blip in enumerate(blips):
            rid = blip.get(qn("r:embed"))
            rel = rels.get(rid)
            if rel is None:
                continue
            target = str(getattr(rel.target_part, "partname", ""))
            name = target.lstrip("/")
            if name not in media:
                continue
            ext = Path(name).suffix or ".png"
            base = caption or f"现场照片_{i + 1}_{k + 1}"
            base = "".join(c for c in base if c not in '\\/:*?"<>|').strip() or f"图_{i + 1}"
            if base in used:
                base += f"_{k + 1}"
            used.add(base)
            fn = outdir / f"{base}{ext}"
            if not fn.exists():
                fn.write_bytes(media[name])
            rel_path = str(fn.relative_to(Path(settings.STORAGE_DIR)))
            items.append({"path": rel_path, "original_name": fn.name})
            paths.append(rel_path)
    return items, paths


def _load_prev_session() -> tuple[list, list, dict]:
    """从上次测试会话的 checkpoint 取**完整材料清单/上传清单**。

    只用这几份手挑的 PDF，附件区会比真实报告少一半（业主 2026-11-03：
    「图片少了很多」）—— 上次会话实际传了 8 份材料 + 70 个文件，这里原样取回。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect("data/workflow_checkpoints.db")
        saver = SqliteSaver(conn)
        for tid in ("bc870865bb6f408d93d783a1ad745e4c_1793674323",
                    "bc870865bb6f408d93d783a1ad745e4c_1793673868"):
            t = saver.get_tuple({"configurable": {"thread_id": tid}})
            st = ((t.checkpoint.get("channel_values") or {}).get("__root__") or {}) if t else {}
            mats = st.get("_project_materials") or []
            ups = st.get("_uploaded_files") or []
            if mats or ups:
                return list(mats), list(ups), dict(st.get("filled_data") or {})
    except Exception as e:
        print(f"[测试数据] checkpoint 读取失败：{e}", flush=True)
    return [], [], {}


async def main() -> int:
    from app.agent.state import create_initial_state
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.llm_service import LLMService
    from app.services.report_assembler import report_assembler
    from app.services.field_fixer import fix_reclamation_fields
    from app.services.reclamation_quantities import (
        enrich_filled_data, QUANTITY_ROWS_KEY, BUDGET_KEY)
    from app.config import settings

    state = create_initial_state(
        session_id=SESSION_ID,
        report_title=TEST_FILLED["project_title"],
        project_context="江苏盐业井神楚州张兴储气库工程三井组临时用地土地复垦方案",
        domain="reclamation",
    )
    state["_domain"] = "reclamation"
    state["_conversation_domain"] = "reclamation"
    state["report_title"] = TEST_FILLED["project_title"]
    state["_report_style"] = "jinhu"
    state["phase"] = "generating"
    state["status"] = "generating"
    state["_skip_analysis"] = True

    filled = state["filled_data"] = dict(TEST_FILLED)
    filled["_company_id"] = 4

    # 按「域 + 地区」选模板：淮安区 → 上传的 11.2 报告模板（含逐节三色画像）
    from app.services.template_selector import resolve_template_path, build_match_text
    state["template_path"] = await resolve_template_path(
        "reclamation",
        build_match_text(filled.get("location"), filled.get("project_name"),
                         state.get("project_context"))) or ""
    print("[模板]", state["template_path"], flush=True)
    # 图件/材料用**上次会话的真实清单**（8 份材料 + 70 个上传文件），
    # 否则附件区只有手挑的几页，业主会看到「图片少了很多」。
    _real_mats, _real_ups, _real_fd = _load_prev_session()
    imgs, meta = _load_prev_images()
    if _real_fd.get("_extracted_images"):
        imgs = _real_fd["_extracted_images"] or imgs
    filled["_extracted_images"] = imgs
    filled.update(meta)
    for _k in ("_centroid_lat", "_centroid_lon"):
        if _real_fd.get(_k) and not filled.get(_k):
            filled[_k] = _real_fd[_k]

    human_imgs, human_img_paths = _load_human112_images()
    if human_img_paths:
        imgs = human_img_paths
        print(f"[图件] 从人工稿 11.2 载入 {len(human_img_paths)} 张", flush=True)

    # 🔴 图3-1 必须用**系统生成**的那张（行政区位/五角星/比例尺/指北针）。
    #    测试会话没有红线 SHP，生成器无法重画；把之前同一项目生成好的图排在最前面，
    #    保证图片目录优先选它，而不是人工稿里那张缺图件要素的旧图。
    from app.config import settings as _st
    _known = Path(_st.STORAGE_DIR) / "images" / "图3-1 工程位置示意图.jpg"
    if _known.exists():
        _rel = "images/图3-1 工程位置示意图.jpg"
        human_imgs.insert(0, {"path": _rel, "original_name": _known.name})
        human_img_paths.insert(0, _rel)
        print("[图件] 使用系统生成的图3-1（含五角星/比例尺/指北针）", flush=True)

    materials = list(_real_mats) + [
        {"source_name": "井神三井组复垦方案与施工组织设计.docx",
         "source_path": "",
         "text_content": DESIGN_MATERIAL},
    ]
    state["_project_materials"] = materials
    state["_uploaded_files"] = list(_real_ups) + human_imgs

    # ① 材料优先：设计参数从 DESIGN_MATERIAL 里抽（不预先写进 filled）
    fix_reclamation_fields(filled, materials, title=TEST_FILLED["project_title"])

    # ①.5 复垦基础资料 Excel（用户每次上传的那张表）—— 表里有的值**覆盖**材料抽取，
    #     对应正式管线 `apply_intake_from_state`（业主 2026-11-04：资料驱动的测试）。
    _intake = os.getenv("RECLAMATION_INTAKE_XLSX", "").strip()
    if _intake:
        from app.services.reclamation_intake import read_intake_workbook, apply_intake
        _data = read_intake_workbook(_intake)
        if _data:
            _changed = apply_intake(filled, _data)
            print(f"[基础资料] {Path(_intake).name}：写入 {len(_changed)} 项", flush=True)
        else:
            print(f"[基础资料] 无法识别：{_intake}", flush=True)

    print("[设计参数]", {k: filled.get(k) for k in (
        "topsoil_thickness", "hard_surface_type", "hard_area_m2",
        "surface_thickness_cm", "service_years", "project_area_m2",
        "project_investment", "construction_nature")}, flush=True)

    # ② 工程量 + 投资估算
    enrich_filled_data(filled, endpoint=os.getenv("RECLAMATION_BUDGET_ENDPOINT", ""))
    print("[工程量]", len(filled.get(QUANTITY_ROWS_KEY) or []), "行；",
          "投资估算" if filled.get(BUDGET_KEY) else "无投资估算", flush=True)
    print("[汇总]", filled.get("earthwork_volume"), flush=True)

    # ③ 逐章生成
    llm = LLMService()
    for n in range(1, 11):
        agent = get_chapter_agent(n, llm_service=llm, domain="reclamation")
        if agent is None:
            continue
        agent._stream_queue = None
        t0 = time.time()
        try:
            await agent.run(state)
            md = (state.get("chapters", {}).get(n) or {}).get("markdown", "")
            print(f"Ch{n}: {len(md)} 字 / {time.time() - t0:.0f}s", flush=True)
        except Exception as e:
            print(f"Ch{n} FAILED: {e}", flush=True)
            traceback.print_exc()
        await asyncio.sleep(1)

    # ④ 装配 —— 🔴 固定文件名，每次**覆盖上一份测试报告**，
    #    不再每次生成一个带时间戳的新文件（业主 2026-11-03）。
    out = report_assembler.assemble(state)
    src = settings.STORAGE_DIR / out
    fixed = settings.STORAGE_DIR / "generated" / "测试_井神三井组_复垦方案_1103.docx"
    fixed.parent.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    if src.resolve() != fixed.resolve():
        _sh.copyfile(src, fixed)
        try:
            src.unlink()
        except OSError:
            pass
    print("OUTPUT", fixed, fixed.exists(), flush=True)
    # 给业主看的那份放到 Downloads（固定名，覆盖上一份）
    try:
        _dl = Path.home() / "Downloads" / "复垦测试报告_井神三井组.docx"
        _sh.copyfile(fixed, _dl)
        print("DOWNLOAD", _dl, flush=True)
    except Exception as _de:
        print("[下载] 复制失败:", _de, flush=True)
    Path("/tmp/reclamation_test_out.txt").write_text(str(fixed), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
