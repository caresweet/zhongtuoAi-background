"""template_compare.py — 生成报告与模板结构对比

对比维度：
1. 章节覆盖度（模板有但生成缺失的章节）
2. 子节覆盖度
3. 结构相似度评分
"""

import json
import re
from typing import Dict, List, Optional


class TemplateComparator:
    """对比生成报告与模板的结构差异。"""

    def compare(self, outline: List[dict], template: Optional[dict],
                chapters: List[dict]) -> dict:
        """返回对比结果。"""
        template_outline = []
        if template and template.get("outline_json"):
            try:
                template_outline = json.loads(template["outline_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        generated_nos = {c.get("chapter_no") for c in chapters}
        template_nos = {ch.get("chapter_no") for ch in template_outline}

        # 模板有但生成缺失
        missing = []
        for tch in template_outline:
            if tch.get("chapter_no") not in generated_nos:
                missing.append(tch)

        # 生成有但模板没有（额外章节）
        extra = []
        for gch in chapters:
            if gch.get("chapter_no") not in template_nos:
                extra.append(gch.get("chapter_no"))

        # 子节覆盖度
        sub_coverage = {}
        for tch in template_outline:
            tno = tch.get("chapter_no")
            gen_ch = next((c for c in chapters if c.get("chapter_no") == tno), None)
            if not gen_ch:
                sub_coverage[tno] = {"template_subs": len(tch.get("subsections", [])),
                                     "covered": 0, "missing_subs": [s["title"] for s in tch.get("subsections", [])]}
                continue
            t_subs = {s.get("title", "") for s in tch.get("subsections", [])}
            g_text = gen_ch.get("markdown", "")
            covered = sum(1 for s in t_subs if s and s[:6] in g_text)
            missing_subs = [s for s in t_subs if s and s[:6] not in g_text]
            sub_coverage[tno] = {"template_subs": len(t_subs), "covered": covered,
                                 "missing_subs": list(missing_subs)}

        # 评分
        total_template = max(len(template_outline), 1)
        covered_count = total_template - len(missing)
        coverage_pct = round(covered_count / total_template * 100, 1)

        summary = (
            f"模板 {total_template} 章，已覆盖 {covered_count} 章（{coverage_pct}%），"
            f"缺失 {len(missing)} 章，额外 {len(extra)} 章。"
        )

        return {
            "summary": summary,
            "coverage_pct": coverage_pct,
            "missing_chapters": missing,
            "extra_chapters": list(extra),
            "sub_coverage": sub_coverage,
        }
