#!/usr/bin/env python3
"""把单章导出成 docx，便于逐章评审。

用法：python3 scripts/export_chapter.py 1 [2 5 ...]
产物：storage/output/第N章_评审稿.docx
"""
import os, pickle, sys
from pathlib import Path
BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND)); sys.path.insert(0, str(BACKEND))

def main():
    nums = [int(x) for x in sys.argv[1:] if x.strip().isdigit()]
    if not nums:
        print("用法: python3 scripts/export_chapter.py 1 [2 ...] [--session <会话id>]"); return
    # 🔴 会话状态统一入口：带 --session 就以**界面会话**为准（界面上填的值才在），
    #    不带才回退离线 pkl 并警告 —— 见 `app/services/session_io.py`
    from app.services.session_io import load_state
    _sid = ""
    _api = ""
    if "--session" in sys.argv:
        _i = sys.argv.index("--session")
        if _i + 1 < len(sys.argv):
            _sid = sys.argv[_i + 1]
    if "--api" in sys.argv:
        _i = sys.argv.index("--api")
        if _i + 1 < len(sys.argv):
            _api = sys.argv[_i + 1]
    state = load_state(_sid, _api, BACKEND / "storage/tmp/review_state.pkl")
    from app.services.survey_data import merge_survey_into_state
    merge_survey_into_state(state)
    state["selected_personnel"] = state.get("selected_personnel") or []
    from app.services.report_assembler import ReportAssembler
    from docx import Document
    from app.agent.chapter_definitions import get_chapter_title
    out_dir = BACKEND / "storage/output"; out_dir.mkdir(parents=True, exist_ok=True)
    for n in nums:
        md = (state.get("chapters", {}).get(n) or {}).get("markdown", "")
        if not md:
            p = out_dir / f"ch{n}.md"
            md = p.read_text(encoding="utf-8") if p.exists() else ""
        # 🔴 第3章=评审表：没有 markdown（由 review_table_filler 渲染制式表），
        #    必须在「无内容」判断之前分流。
        _is_review = (n == 3)
        if not md and not _is_review:
            print(f"第{n}章无内容"); continue
        a = ReportAssembler()
        # 🔴 按会话 state 钉住域 —— 否则 `_prepare_image_catalog` 会用类默认值
        #    "stability"，复垦单章导出会被盖上稳评图注（`assemble()` 里同样一行）。
        from app.domains import domain_of_state
        a._domain = domain_of_state(state)
        a._inserted_images = set(); a._chapter_image_counter = {}
        a._system_rendered_tables = set(); a._rendered_extracted_tbls = set()
        a._session_state = state
        # 🔴 与正式报告一致：勾选人员决定前置工作组名单与签字表
        a._selected_personnel = state.get("selected_personnel") or \
            (state.get("filled_data", {}) or {}).get("_selected_personnel") or []
        # 🔴 「选择证书」独立多选框（最多 3 人）→ 前置「公司资质证书」区的人员证书图
        a._selected_cert_personnel = [str(x).strip() for x in (
            state.get("selected_cert_personnel") or
            (state.get("filled_data", {}) or {}).get("_selected_cert_personnel") or [])
            if str(x).strip()][:3]
        d = Document()
        a._setup_page(d)
        # 🔴 图库/问卷统计必须与完整报告走**同一条路**：
        #    以前单章导出不建图库 → _chapter_image_map 为空 →
        #    「现场公示 / 实地勘察 / 座谈会」的照片一张都不出现，正文全是干文字。
        _img_files = a._prepare_image_catalog(state)
        _stats = a._get_survey_stats(state)
        if _is_review:
            from app.services.review_table_filler import fill_review_table_into
            a._add_chapter_title_page(d, "三", get_chapter_title(3))
            # 🔴 与正式报告一致：评审表那页**不整体居中**，按正常正文从上到下排。
            #    （章标题页仍居中，由 `_add_chapter_title_page` 内部处理。）
            fill_review_table_into(d, state.get("filled_data", {}),
                                   state.get("chapters", {}),
                                   (state.get("filled_data", {}) or {}).get("_extracted_tables") or [],
                                   state.get("template_path"))
        else:
            # 🔴 与正式报告一致：第4章的「参与人员签字」表换成系统渲染标记
            #    （用勾选人员 + 手写签名图），否则单章稿会显示 LLM 抄来的老名单。
            if n == 4:
                from app.agent.chapter_definitions import prepare_chapter4_markdown
                md = prepare_chapter4_markdown(md)
            # 🔴 与正式报告一致：第6章「（一）项目概况」按固定骨架重排（标题系统写死）
            if n == 6:
                from app.agent.chapter_definitions import prepare_chapter6_markdown
                md = prepare_chapter6_markdown(md)
            # 🔴 与正式报告一致：第7章「工作计划」把用户填的工作程序时间落位
            if n == 7:
                from app.services.work_schedule import apply_to_chapter7
                md, _ = apply_to_chapter7(md, state.get("filled_data", {}))
            # 🔴 PDF 提取表（勘测定界表6-1 等）**按需**传：本章正文里有
            #    `[SYS_TABLE:…]` 标记才传，且只传本章用得上的那几张。
            #    全量塞 `filled["_extracted_tables"]` 会把同一张土地表按条数重复渲染；
            #    一张不传又会让第6章的标记渲染不出来、被当正文原样印出去。
            #    必须和完整报告走同一个来源：`assemble()` 用的就是
            #    `_get_extracted_tables(state).get(ch_num, [])`。
            _xt = []
            if "[SYS_TABLE:" in md:
                _xt = ReportAssembler._get_extracted_tables(state).get(n, [])
            a._add_chapter(d, n, md, _img_files, _stats, _xt, state.get("filled_data", {}))
        # 🔴 跑完整后处理，否则单章评审稿看不到一级标题格式/字体归一化等效果
        try:
            a._post_generation_fix(d, state.get("filled_data", {}))
        except Exception as e:
            print(f"   ⚠️ 后处理跳过: {e}")
        try:
            a.ensure_all_tables_bordered(d)
        except Exception:
            pass
        fp = out_dir / f"第{n}章_{get_chapter_title(n)}_评审稿.docx"
        d.save(str(fp))
        print(f"✅ {fp}")

if __name__ == "__main__":
    main()
