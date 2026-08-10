"""sequential_engine.py — 顺序化报告生成引擎

流程（串行，一步一思）：
  1. 需求确认
  2. 检索固定资料
  3. 分析用户资料
  4. 扫描附件图片（稳评照片/招标截图）
  5. 生成大纲（含表格/图片结构）
  6. 逐章写作 → 逐章审核 → 不过则重写
  7. 组装完整报告
  8. 终审
  9. 与模板对比
  10. 输出：基于模板 docx 填入内容（保留表格+图片+格式+附件）

每步通过 yield 事件与用户"一句话一句话"交流。
"""

import asyncio
import gc
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from .db import DualKB, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING
from .llm import LLMClient
from .guardrails import ReviewGuard
from .material_reader import MaterialReader

_OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ── 领域配置 ────────────────────────────────────────────────────
_DOMAIN_CONFIG = {
    KB_DOMAIN_STABILITY: {
        "name": "社会稳定风险评估报告",
        "company": "江苏众拓项目代理咨询有限公司",
        "fixed_asset_types": ["营业执照", "资质证书", "人员证件", "人员", "项目资料"],
        "report_title": "社会稳定风险评估报告",
        "image_categories": {
            "公示照片4": "决策公示照片",
            "临时用地现场照片": "拟征收地块现场照片",
            "村民开会现场": "群众座谈会照片",
            "群众座谈会扫描": "群众座谈会资料",
            "专家评审会照片": "专家评审会照片",
            "稳评专家意见扫描": "稳评专家意见",
            "图片-稳评（报告中": "稳评报告中图片",
        },
        "material_dirs": [
            "/Users/mac/Downloads/稳评资料",
        ],
    },
    KB_DOMAIN_BIDDING: {
        "name": "招标投标文件",
        "company": "江苏众拓测绘有限公司",
        "fixed_asset_types": ["营业执照", "资质证书", "人员", "财务报告", "社保纳税",
                              "法人证明", "授权委托", "承诺函", "设备", "业绩"],
        "report_title": "响应文件",
        "image_categories": {},
        "material_dirs": [],
    },
}


class SequentialEngine:
    """顺序化报告生成引擎。"""

    def __init__(self, db: DualKB, llm: Optional[LLMClient] = None,
                 guard: Optional[ReviewGuard] = None):
        self.db = db
        self.llm = llm or LLMClient()
        self.guard = guard or ReviewGuard()
        self.reader = MaterialReader(llm=self.llm)

    # ── 主入口 ──────────────────────────────────────────────────
    async def generate(
        self, domain: str, requirement: str,
        material_paths: List[str],
        project_name: str = "",
    ) -> AsyncGenerator[Tuple[str, str or dict], None]:
        cfg = _DOMAIN_CONFIG.get(domain)
        if not cfg:
            yield "error", f"未知领域: {domain}"
            return

        run_id = uuid.uuid4().hex[:12]
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # ── Step 1: 需求确认 ────────────────────────────────────
        yield "think", f"用户需要生成「{cfg['name']}」，项目：{project_name or '未指定'}。我先确认需求。"
        yield "say", f"好的，我将为您生成{cfg['name']}。项目名称：{project_name or '待确认'}。让我先检索固定资料。"

        # ── Step 2: 检索固定资料 ────────────────────────────────
        yield "think", "检索知识库中的固定资料：营业执照、资质证书、人员证件等。"
        fixed_assets = self.db.get_fixed_assets(domain, cfg["fixed_asset_types"])
        asset_summary = self._summarize_fixed_assets(fixed_assets)
        if fixed_assets:
            yield "say", f"已检索到 {len(fixed_assets)} 项固定资料：{asset_summary}。这些将自动填入报告。"
        else:
            yield "say", "知识库中暂无固定资料，相关部分将根据公司信息生成。"

        # ── Step 3: 分析用户资料 ────────────────────────────────
        yield "think", f"分析用户提供的 {len(material_paths)} 份资料。"
        materials = []
        if material_paths:
            yield "say", f"正在分析您提供的 {len(material_paths)} 份资料，请稍候..."
            materials = await self.reader.read_many(material_paths)
            mat_summary = self._summarize_materials(materials)
            yield "say", f"资料分析完成。{mat_summary}"
        else:
            yield "say", "未提供项目资料，将基于需求描述和模板生成。"

        # ── Step 3b: 扫描附件图片 ──────────────────────────────
        attachment_images: Dict[str, List[str]] = {}
        image_descriptions: Dict[str, str] = {}
        material_dirs = cfg.get("material_dirs", [])
        image_cats = cfg.get("image_categories", {})

        if material_dirs and image_cats:
            yield "think", "扫描稳评资料目录中的照片文件。"
            yield "say", "正在扫描附件照片..."
            for dir_path in material_dirs:
                for sub_name, cat_label in image_cats.items():
                    sub_dir = os.path.join(dir_path, sub_name)
                    if not os.path.isdir(sub_dir):
                        continue
                    imgs = self._scan_images(sub_dir)
                    if imgs:
                        attachment_images[cat_label] = imgs
                        # 不做 OCR，只记录分类和数量（避免 OOM）
                        image_descriptions[cat_label] = f"{len(imgs)}张{cat_label}照片，文件名：{', '.join(os.path.basename(p) for p in imgs[:5])}"

            if attachment_images:
                total_imgs = sum(len(v) for v in attachment_images.values())
                yield "say", f"扫描到 {total_imgs} 张附件照片，分 {len(attachment_images)} 类：{', '.join(attachment_images.keys())}。"
            else:
                yield "say", "未扫描到附件照片。"

        # ── Step 4: 生成大纲 ────────────────────────────────────
        yield "think", "基于知识库中已学的模板大纲，生成本次报告大纲。"
        learned = self.db.get_learned_chapters(domain)
        if not learned:
            yield "think", "知识库中无已学模板，使用默认章节结构。"
            learned = self._default_outline(domain)

        outline = [ch for ch in learned]
        # 修复推断标题：Ch7 应为"风险防范与化解措施"
        for ch in outline:
            if "推断标题" in ch.get("title", ""):
                ch_no = ch.get("chapter_no", 7)
                inferred_titles = {6: "风险估计", 7: "风险防范与化解措施"}
                ch["title"] = inferred_titles.get(ch_no, f"第{ch_no}章")
                yield "think", f"修复推断标题：第{ch_no}章 → {ch['title']}"
        outline_text = self._format_outline(outline)
        tables_info = self._format_tables_in_outline(outline)
        images_info = self._format_images_in_outline(outline)
        yield "say", f"根据模板章节结构，报告大纲如下：\n\n{outline_text}"
        if tables_info:
            yield "say", f"模板包含以下表格需要填写：\n{tables_info}"
        if images_info:
            yield "say", f"模板包含以下图片位置需要插入：\n{images_info}"

        self.db.create_run(domain, run_id, project_name, requirement, outline)

        # ── Step 5: 逐章写作 + 审核 ─────────────────────────────
        yield "think", f"开始逐章生成，共 {len(outline)} 章。每章写完后自动审核。"
        chapter_results: List[dict] = []
        chapter_contents: Dict[int, str] = {}
        fixed_context = self._build_fixed_context(fixed_assets, cfg)
        material_context = self._build_material_context(materials)
        image_context = self._build_image_context(image_descriptions)

        for idx, ch in enumerate(outline):
            ch_no = ch.get("chapter_no", idx + 1)
            ch_title = ch.get("title", f"第{ch_no}章")
            yield "think", f"正在撰写第 {ch_no} 章「{ch_title}」..."
            yield "say", f"📝 正在撰写第 {ch_no} 章：{ch_title}"
            yield "progress", {"chapter": ch_no, "total": len(outline), "phase": "writing"}

            content = await self._write_chapter(
                domain, ch, outline, fixed_context, material_context,
                requirement, cfg, image_context
            )

            review = self.guard.review(content, ch_title)
            attempts = 1
            while not review.passed and attempts < 3:
                yield "think", f"第 {ch_no} 章审核未通过：{review.suggestions}。重写中（第 {attempts+1} 次）..."
                yield "say", f"⚠️ 第 {ch_no} 章审核发现问题，正在修正..."
                content = await self._rewrite_chapter(
                    domain, ch, content, review.suggestions, outline,
                    fixed_context, material_context, requirement, cfg
                )
                review = self.guard.review(content, ch_title)
                attempts += 1

            if review.passed:
                yield "say", f"✅ 第 {ch_no} 章「{ch_title}」审核通过。"
            else:
                yield "say", f"⚠️ 第 {ch_no} 章经 {attempts} 次修改仍有问题：{review.suggestions}。先保留，终审时处理。"

            yield "progress", {"chapter": ch_no, "total": len(outline), "phase": "done",
                               "status": "passed" if review.passed else "warning"}

            chapter_results.append({
                "chapter_no": ch_no,
                "title": ch_title,
                "markdown": content,
                "status": "passed" if review.passed else "warning",
                "review_result": {"passed": review.passed, "issues": review.issues},
                "attempts": attempts,
            })
            chapter_contents[ch_no] = content

            self.db.update_run_chapters(domain, run_id, chapter_results)
            # 释放中间对象防止 OOM
            gc.collect()

        # ── Step 6: 组装 ────────────────────────────────────────
        yield "think", "所有章节完成，组装完整报告。"
        yield "say", "📋 所有章节已生成，正在组装完整报告..."
        full_report = self._assemble_report(chapter_results, cfg, project_name)

        # ── Step 7: 终审 ────────────────────────────────────────
        yield "think", "终审：检查全文是否有占位词、口语化、结构缺失。"
        yield "say", "🔍 正在进行终审..."
        final_review = self._final_review(full_report, chapter_results)
        yield "say", f"终审结果：{final_review['summary']}"

        # ── Step 8: 与模板对比 ───────────────────────────────────
        yield "think", "与知识库模板进行结构对比，检查章节覆盖度。"
        template = self.db.get_primary_template(domain)
        comparison = self._compare_with_template(outline, template, chapter_results)
        yield "say", f"📊 模板对比：{comparison['summary']}"

        missing = comparison.get("missing_chapters", [])
        if missing:
            yield "think", f"发现 {len(missing)} 个模板要求但未生成的章节，退回补充。"
            yield "say", f"🔧 发现 {len(missing)} 个缺失章节，正在补充..."
            for miss in missing:
                ch_no = miss.get("chapter_no", len(chapter_results) + 1)
                ch_title = miss.get("title", f"第{ch_no}章")
                yield "say", f"📝 补充第 {ch_no} 章：{ch_title}"
                content = await self._write_chapter(
                    domain, miss, outline + missing, fixed_context, material_context,
                    requirement, cfg, image_context
                )
                review = self.guard.review(content, ch_title)
                chapter_results.append({
                    "chapter_no": ch_no, "title": ch_title, "markdown": content,
                    "status": "passed" if review.passed else "warning",
                    "review_result": {"passed": review.passed, "issues": review.issues},
                    "attempts": 1,
                })
                chapter_contents[ch_no] = content
            full_report = self._assemble_report(chapter_results, cfg, project_name)
            self.db.update_run_chapters(domain, run_id, chapter_results)

        self.db.save_final_review(domain, run_id, final_review)
        self.db.save_comparison(domain, run_id, comparison)

        # ── Step 9: 输出 ────────────────────────────────────────
        yield "think", "保存最终报告文件（基于模板 docx 填入内容）。"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = (project_name or cfg["report_title"]).replace("/", "-")[:40]

        md_path = _OUTPUT_DIR / f"{safe_name}_{domain}_{ts}.md"
        md_path.write_text(full_report, encoding="utf-8")

        template_path = template.get("file_path", "") if template else ""
        docx_path = _OUTPUT_DIR / f"{safe_name}_{domain}_{ts}.docx"

        if template_path and os.path.exists(template_path):
            yield "say", "📝 正在基于模板生成 Word 文档（保留表格和格式）..."
            try:
                from .template_docx_writer import TemplateDocxWriter
                TemplateDocxWriter().write_with_template(
                    template_path=template_path,
                    chapter_contents=chapter_contents,
                    output_path=str(docx_path),
                    project_name=project_name,
                    attachment_images=attachment_images,
                )
                yield "say", "✅ Word 文档已基于模板生成，保留表格结构和格式。"
            except Exception as e:
                yield "think", f"模板填充失败: {e}，回退到纯 Markdown 转 Word。"
                from .template_docx_writer import TemplateDocxWriter
                TemplateDocxWriter().write_from_markdown(
                    full_report, str(docx_path), title=cfg["report_title"]
                )
        else:
            from .template_docx_writer import TemplateDocxWriter
            TemplateDocxWriter().write_from_markdown(
                full_report, str(docx_path), title=cfg["report_title"]
            )

        self.db.update_run_status(domain, run_id, "completed", str(docx_path))

        yield "say", f"✅ 报告生成完成！\n\n📄 Markdown：{md_path}\n📄 Word：{docx_path}"
        yield "progress", {"chapter": len(outline), "total": len(outline), "phase": "complete",
                           "output": str(docx_path)}
        yield "complete", {"run_id": run_id, "output": str(docx_path),
                           "md_output": str(md_path), "chapters": len(chapter_results)}

    # ── 章节写作 ────────────────────────────────────────────────
    async def _write_chapter(self, domain, ch, outline, fixed_ctx, mat_ctx,
                             requirement, cfg, image_ctx="") -> str:
        guide = ch.get("writing_guide", "")
        subs = ch.get("subsections", [])
        sub_list = "\n".join(f"  - {s['title']}" for s in subs) if subs else "  （无固定子节）"
        tables = ch.get("tables", [])
        table_desc = self._format_table_for_prompt(tables)
        images = ch.get("images", [])
        image_desc = self._format_image_for_prompt(images)

        system = (
            f"你是{cfg['name']}编写专家，隶属于{cfg['company']}。"
            "严格按模板结构和写作指引撰写章节内容。\n"
            "要求：\n"
            "1. 内容必须完整、专业、正式书面语\n"
            "2. 禁止出现【待补充】【后续提供】【待完善】等占位词\n"
            "3. 禁止口语化表达\n"
            "4. 禁止暴露AI身份或元注释\n"
            "5. 所有数据来自提供的资料或固定信息，不编造\n"
            "6. 使用Markdown格式，标题用「数字. 标题」格式\n"
            "7. **表格输出规则（最重要）：**\n"
            "   - 如模板有表格，必须输出完整 Markdown 表格\n"
            "   - 表头列名必须与模板一致\n"
            "   - fill_type=fixed/structural 的列：照抄模板值（评分标准、行分类等不变）\n"
            "   - fill_type=project_data 的列：必须用用户资料中的实际数据替换模板示例值\n"
            "     * 如果用户资料有确切数据（如调查问卷反对率），直接使用\n"
            "     * 如果用户资料不包含具体数值，根据资料中的定性描述和专业判断给出合理估算值\n"
            "     * 禁止照抄模板的0值/空值作为最终结果，必须给出分析后的合理数值\n"
            "   - fill_type=calculated 的列：根据项目情况计算填入\n"
            "   - 禁止照抄模板示例数据到 project_data 列\n"
            "8. 如果模板中有图片位置，在对应位置标注【插入图片：描述】\n"
        )
        prompt = (
            f"## 报告需求\n{requirement}\n\n"
            f"## 当前章节\n第{ch.get('chapter_no','')}章：{ch.get('title','')}\n\n"
            f"## 模板子节结构\n{sub_list}\n\n"
            f"## 写作指引\n{guide}\n\n"
            f"## 模板表格结构（必须按此结构输出表格数据）\n{table_desc}\n\n"
            f"## 模板图片位置\n{image_desc}\n\n"
            f"## 固定资料（公司信息等）\n{fixed_ctx}\n\n"
            f"## 用户提供的项目资料\n{mat_ctx}\n\n"
            f"## 附件图片描述（供报告中引用）\n{image_ctx}\n\n"
            "请撰写该章节的完整内容（800-2500字），直接输出正文，不要加说明。\n"
            "重要：如有表格数据，必须输出完整的Markdown表格。\n"
            "如有图片位置，标注【插入图片：图片类型描述】。"
        )
        try:
            content = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system, max_tokens=4000, temperature=0.4,
            )
            return content.strip()
        except Exception as e:
            return f"## {ch.get('title','')}\n\n[章节生成失败: {e}]"

    async def _rewrite_chapter(self, domain, ch, prev_content, feedback,
                               outline, fixed_ctx, mat_ctx, requirement, cfg) -> str:
        system = (
            f"你是{cfg['name']}编写专家。之前生成的章节未通过审核，请根据审核意见修改。"
            "同样禁止占位词、口语化、AI元注释。直接输出修改后的完整正文。"
            "如有表格数据，必须输出完整的Markdown表格。"
        )
        prompt = (
            f"## 章节标题\n第{ch.get('chapter_no','')}章：{ch.get('title','')}\n\n"
            f"## 审核问题\n{feedback}\n\n"
            f"## 原内容\n{prev_content}\n\n"
            f"## 固定资料\n{fixed_ctx}\n\n"
            f"## 项目资料\n{mat_ctx}\n\n"
            "请修改上述问题，输出完整章节内容。如有表格请保留Markdown表格。"
        )
        try:
            return (await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system, max_tokens=4000, temperature=0.3,
            )).strip()
        except Exception:
            return prev_content

    # ── 组装 ────────────────────────────────────────────────────
    def _assemble_report(self, chapters, cfg, project_name) -> str:
        title = cfg["report_title"]
        header = f"# {project_name or title}\n\n"
        header += f"> 编制单位：{cfg['company']}\n"
        header += f"> 编制日期：{datetime.now().strftime('%Y年%m月%d日')}\n\n---\n\n"
        body = "\n\n---\n\n".join(
            ch["markdown"] for ch in sorted(chapters, key=lambda c: c.get("chapter_no", 0))
        )
        return header + body

    # ── 终审 ────────────────────────────────────────────────────
    def _final_review(self, full_report, chapters) -> dict:
        all_hits = self.guard.quick_scan(full_report)
        passed_count = sum(1 for c in chapters if c["status"] == "passed")
        warning_count = len(chapters) - passed_count
        total_chars = len(full_report)
        summary = (
            f"全文 {total_chars} 字，{len(chapters)} 章。"
            f"通过 {passed_count} 章，待改进 {warning_count} 章。"
            f"占位词命中 {len(all_hits)} 处：{', '.join(set(all_hits)) if all_hits else '无'}。"
        )
        return {
            "summary": summary, "total_chars": total_chars,
            "chapter_count": len(chapters), "passed": passed_count,
            "warnings": warning_count, "placeholder_hits": all_hits,
        }

    # ── 模板对比 ────────────────────────────────────────────────
    def _compare_with_template(self, outline, template, chapters) -> dict:
        from .template_compare import TemplateComparator
        return TemplateComparator().compare(outline, template, chapters)

    # ── 辅助 ────────────────────────────────────────────────────
    def _scan_images(self, directory: str) -> List[str]:
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
        paths = []
        for f in os.listdir(directory):
            fp = os.path.join(directory, f)
            if os.path.isfile(fp) and Path(f).suffix.lower() in exts:
                paths.append(fp)
        return sorted(paths)

    def _summarize_fixed_assets(self, assets) -> str:
        if not assets:
            return "无"
        types = {}
        for a in assets:
            t = a.get("asset_type", "其他")
            types[t] = types.get(t, 0) + 1
        return "、".join(f"{t}({n})" for t, n in types.items())

    def _summarize_materials(self, materials) -> str:
        valid = [m for m in materials if m.get("text") and not m["text"].startswith("[")]
        total_chars = sum(len(m.get("text", "")) for m in valid)
        return f"成功解析 {len(valid)}/{len(materials)} 份，共 {total_chars} 字。"

    def _format_outline(self, outline) -> str:
        lines = []
        for ch in outline:
            lines.append(f"**第{ch.get('chapter_no','')}章 {ch.get('title','')}**")
            for sub in ch.get("subsections", []):
                lines.append(f"  - {sub.get('title','')}")
        return "\n".join(lines)

    def _format_tables_in_outline(self, outline) -> str:
        lines = []
        for ch in outline:
            tables = ch.get("tables", [])
            if tables:
                for t in tables:
                    header_str = " | ".join(t.get("headers", [])[:5])
                    lines.append(
                        f"  第{ch.get('chapter_no','')}章 — 表格{t.get('idx','')}: "
                        f"{t.get('rows','')}行×{t.get('cols','')}列, "
                        f"表头: [{header_str}]"
                    )
        return "\n".join(lines) if lines else ""

    def _format_images_in_outline(self, outline) -> str:
        lines = []
        for ch in outline:
            images = ch.get("images", [])
            if images:
                for img in images:
                    lines.append(
                        f"  第{ch.get('chapter_no','')}章 — 图片{img.get('idx','')}: "
                        f"题注=\"{img.get('caption','')}\""
                    )
        return "\n".join(lines) if lines else ""

    def _format_table_for_prompt(self, tables: List[dict]) -> str:
        """格式化表格信息给写作 prompt。

        关键改进：明确标注每列的填充类型。
        - fixed/structural 列：照抄模板骨架（如评分标准、行分类）
        - project_data 列：必须从用户资料提取实际数据（如事项名称、得分）
        - calculated 列：根据其他数据计算（如反对率百分比、综合得分）
        """
        if not tables:
            return "本章无模板表格"
        parts = []
        for t in tables:
            header_str = " | ".join(t.get("headers", [])[:6])
            fill_map = t.get("fill_map", [])
            row_labels = t.get("row_labels", [])

            parts.append(
                f"表格{t.get('idx','')}: {t.get('rows','')}行×{t.get('cols','')}列\n"
                f"表头: {header_str}"
            )

            # 各列填充规则（核心改动）
            if fill_map:
                parts.append("各列数据来源（重要！勿照抄模板示例值）：")
                for fm in fill_map:
                    ft = fm.get("fill_type", "project_data")
                    if ft == "fixed" or ft == "structural":
                        parts.append(f"  - {fm['header']}: 【保持模板原样】{fm['description']}")
                    elif ft == "project_data":
                        parts.append(f"  - {fm['header']}: 【必须从用户资料替换】{fm['description']}，模板值仅为示例！")
                    elif ft == "calculated":
                        parts.append(f"  - {fm['header']}: 【需要计算】{fm['description']}")

            # 骨架行标签
            if row_labels:
                parts.append(f"行分类（保持不变）: {row_labels[:6]}")

            # 模板示例值（明确标注为示例，不应照抄）
            example = t.get("example_values", [])
            if example:
                example_str = ""
                for row in example[:2]:
                    example_str += " | ".join(row[:5]) + "\n"
                parts.append(f"模板示例值（仅供参考格式，project_data列必须替换为实际数据）:\n{example_str}")

            parts.append("要求：必须输出完整的 Markdown 表格，列数和行数与模板一致。")

        return "\n".join(parts)

    def _format_image_for_prompt(self, images: List[dict]) -> str:
        if not images:
            return "本章无模板图片位置"
        parts = []
        for img in images:
            parts.append(
                f"图片位置: 题注=\"{img.get('caption','')}\"\n"
                f"请在此处标注【插入图片：{img.get('caption','')}】"
            )
        return "\n".join(parts)

    def _build_fixed_context(self, assets, cfg) -> str:
        if not assets:
            return f"公司：{cfg['company']}（固定资料暂无，按公司信息撰写）"
        parts = [f"公司：{cfg['company']}"]
        for a in assets:
            atype = a.get("asset_type", "")
            title = a.get("title", "")
            # 项目资料：优先输出 structured_json 汇总（统计数据），
            # 再补充 extracted_text 详情（OCR原文）
            if atype == "项目资料" and a.get("structured"):
                import json as _json
                summary = _json.dumps(a["structured"], ensure_ascii=False)
                raw = a.get("extracted_text", "") or ""
                # structured_json 汇总 + extracted_text 前2000字详情
                parts.append(
                    f"【{atype}】{title} — 统计汇总：{summary}\n"
                    f"原始数据节选：{raw[:2000]}"
                )
            elif atype == "参考资料" and a.get("structured"):
                import json as _json
                note = _json.dumps(a["structured"], ensure_ascii=False)
                parts.append(f"【{atype}】{title}：{note}")
            else:
                t = a.get("extracted_text", "") or title
                if t:
                    max_len = 500
                    parts.append(f"【{atype}】{title}：{t[:max_len]}")
        return "\n".join(parts)

    def _build_material_context(self, materials) -> str:
        if not materials:
            return "（用户未提供项目资料）"
        parts = []
        for m in materials:
            text = m.get("text", "")
            if text and not text.startswith("["):
                parts.append(f"【{m.get('name','')}】({m.get('method','')})：{text[:2000]}")
        return "\n\n".join(parts) if parts else "（资料解析无有效文本）"

    def _build_image_context(self, image_descriptions: Dict[str, str]) -> str:
        if not image_descriptions:
            return "（无附件照片描述）"
        parts = []
        for cat, desc in image_descriptions.items():
            parts.append(f"【{cat}】{desc}")
        return "\n\n".join(parts)

    def _default_outline(self, domain) -> List[dict]:
        if domain == KB_DOMAIN_STABILITY:
            return [
                {"chapter_no": i+1, "title": t, "level": 1, "subsections": [],
                 "writing_guide": "", "required_data": [], "tables": [], "images": []}
                for i, t in enumerate([
                    "拟征收决策基本概况", "社会稳定风险评估过程、方法和依据",
                    "社会稳定风险因素调查", "拟征收决策的综合分析",
                    "社会稳定风险因素识别", "风险估计",
                    "风险防范与化解措施", "措施后风险等级评估",
                    "社会稳定风险评估结论", "应急预案",
                ])
            ]
        else:
            return [
                {"chapter_no": i+1, "title": t, "level": 1, "subsections": [],
                 "writing_guide": "", "required_data": [], "tables": [], "images": []}
                for i, t in enumerate([
                    "投标函及投标函附录", "法定代表人身份证明及授权委托书",
                    "投标保证金", "资格审查文件",
                    "技术标", "商务标",
                    "项目管理机构", "其他材料",
                ])
            ]
