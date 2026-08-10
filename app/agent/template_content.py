"""模板报告内容复用 — 从历史报告中提取可复用段落。

规则：
- FIXED: 法律条文、标准流程、公司资质、应急预案框架 → 原文保留，仅改项目名/日期
- VARIABLE: 项目数据、调查统计、风险分析、评估结论 → 参照结构，替换内容
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

# 章节分类：哪些是固定内容，哪些可变
FIXED_CHAPTER_KEYWORDS = {
    2: ['法律', '法规', '规章', '文件', '理由', '目的', '原则'],
    3: ['法律', '法规', '规章', '政策', '文件', '委托'],
    5: ['总则', '思想', '原则', '组织', '职责', '预警', '处置', '舆情', '保障', '奖惩'],
    6: ['思想', '原则', '范围', '方法', '步骤', '过程', '时间', '人员', '职责'],
    10: ['原则', '程序', '手续', '补偿', '宣传', '资金', '公示', '协议', '社保', '信访'],
    12: ['资质', '营业执照', '证书', '人员', '委托', '红线', '公告'],
}

VARIABLE_CHAPTER_KEYWORDS = {
    1: ['基本情况', '利益相关者', '位置', '面积', '户数'],
    4: ['基本情况', '利益相关者', '位置', '面积', '户数'],
    7: ['调查', '问卷', '走访', '座谈', '范围', '对象', '意见', '诉求'],
    8: ['合法性', '合理性', '可行性', '可控性'],
    9: ['风险因素', '预测', '估计', '方法', '等级', '量化'],
    11: ['结论', '建议', '签字'],
}


class TemplateContentProvider:
    """从历史模板报告中提供可复用内容"""

    def __init__(self):
        self._chapters = None

    def _load(self) -> dict:
        if self._chapters is not None:
            return self._chapters

        # Try multiple paths to find template_chapters.json
        search_paths = [
            Path(__file__).parent.parent.parent.parent / "data" / "template_chapters.json",
            Path.cwd() / "data" / "template_chapters.json",
        ]
        for cache_path in search_paths:
            try:
                if cache_path.exists():
                    self._chapters = json.loads(cache_path.read_text(encoding='utf-8'))
                    return self._chapters
            except Exception:
                pass

        self._chapters = {}
        return self._chapters

    def get_chapter_content(self, chapter_num: int) -> List[dict]:
        """Get template content sections for a chapter."""
        data = self._load()
        return data.get(str(chapter_num), [])

    def classify_section(self, chapter_num: int, heading: str) -> str:
        """Classify a section as 'fixed', 'variable', or 'mixed'."""
        fixed_kw = FIXED_CHAPTER_KEYWORDS.get(chapter_num, [])
        variable_kw = VARIABLE_CHAPTER_KEYWORDS.get(chapter_num, [])

        is_fixed = any(kw in heading for kw in fixed_kw)
        is_variable = any(kw in heading for kw in variable_kw)

        if is_fixed and not is_variable:
            return 'fixed'
        elif is_variable and not is_fixed:
            return 'variable'
        else:
            return 'mixed'

    def build_template_prompt(self, chapter_num: int, project_data: dict) -> str:
        """Build template reference section showing the writing benchmark.

        Shows a sample paragraph from the template with analysis of what makes it good,
        so the LLM learns to match the style without copying the text.
        """
        sections = self.get_chapter_content(chapter_num)
        if not sections:
            return ""

        # Pick the longest, best section as the benchmark
        benchmark = max(sections, key=lambda s: len(s.get('t', '')))
        template_text = benchmark.get('t', '')
        if len(template_text) < 100:
            return ""

        # Show the template paragraph + analysis
        parts = [
            f"\n## 📋 写作标杆 — 模板报告本章写法示例",
            f"以下是模板报告中本章一个典型段落的写法。注意其特点：",
            f"- 段落长度: {len(template_text)}字（你的输出每个子标题下总计应达到400-800字）",
            f"- 结构: 政策引用→项目现状→分析论证→结论",
            f"- 语言: 政府公文语体，数据与论述紧密结合",
            f"",
            f"### 模板段落示例（学习其风格、深度和结构）",
            f"```",
            f"{template_text[:1500]}",
            f"```",
            f"",
            f"### 你的任务",
            f"用本项目的数据（位置、面积、户数、问卷结果等），",
            f"以**同等的段落长度和分析深度**撰写本章内容。",
            f"每个子标题下至少400字，结构与上述示例一致。",
        ]
        return "\n".join(parts)


template_content_provider = TemplateContentProvider()
