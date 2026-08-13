"""报告拆解 — LLM 驱动，把范文/爬取结果拆解成可复用的 schema。

拆解输出（可注册为 DomainConfig + 供 GenericChapterAgent 消费）：
- 章节结构（几章/每章讲什么/需什么数据）
- 评分规则（如有量化评分）
- 数据字段（需用户提供什么）
- 措辞风格（公文特征/禁用词）
- 表格模板（列结构）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DecomposedSchema:
    """拆解出的报告 schema（数据，非代码）。"""
    domain_id: str = ""
    display_name: str = ""
    chapter_structure: Dict[int, Dict] = field(default_factory=dict)
    scoring_rules: Optional[Dict] = None
    data_fields: List[str] = field(default_factory=list)
    style_notes: str = ""
    table_templates: List[Dict] = field(default_factory=list)


class ReportDecomposer:
    """把范文拆解成结构化 schema。"""

    async def decompose(
        self,
        report_text: str,
        report_type: str,
        crawled_reports: Optional[List] = None,
        llm_service=None,
    ) -> Optional[DecomposedSchema]:
        """拆解一份范文，输出可复用的 schema。

        Args:
            report_text: 范文全文（清洗后的骨架）
            report_type: 报告类型名称
            crawled_reports: 联网爬取的提炼结果（可选，补充章节结构）
            llm_service: LLM 服务
        """
        if not llm_service:
            logger.warning("No LLM service for decomposition")
            return None

        # 拼接爬取结果作为补充参考
        crawled_hint = ""
        if crawled_reports:
            for cr in crawled_reports[:2]:
                ch = "、".join(cr.chapter_structure[:10])
                crawled_hint += f"\n- {cr.title}: 章节={ch}；要点={cr.key_points[:200]}"

        prompt = f"""你是报告结构拆解专家。请把「{report_type}」的范文拆解成可复用的结构化 schema。

## 范文内容（已清洗骨架）
{report_text[:6000]}

## 联网补充参考
{crawled_hint if crawled_hint else "（无）"}

## 拆解要求
1. **章节结构**：识别报告分几章、每章标题、每章核心内容要点、每章需要哪些数据
2. **评分规则**：如果这类报告有量化评分，列出评分维度/指标/权重/等级阈值
3. **数据字段**：写这类报告需要用户提供哪些数据（如项目名称、位置、面积、投资额等）
4. **措辞风格**：公文特征、禁用词、专业术语
5. **表格模板**：报告中有哪些标准表格，列出每张表的列结构

## 输出 JSON 格式
```json
{{
  "domain_id": "英文短id，如 feasibility_study",
  "display_name": "报告类型中文名",
  "chapters": [
    {{
      "number": 1,
      "title": "章节标题",
      "key_points": ["要点1", "要点2"],
      "data_needed": ["字段key1", "字段key2"]
    }}
  ],
  "scoring_rules": null 或 {{"dimensions": [{{"name":"合法性","weight":10}}], "risk_bands":[{{"max":15,"level":"低风险"}}]}},
  "data_fields": ["字段1", "字段2"],
  "style_notes": "措辞风格说明",
  "table_templates": [{{"name":"表名","columns":["列1","列2"]}}]
}}
```"""

        try:
            result = await llm_service.chat_with_reasoning(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000, temperature=0.2,
            )
            content = result.get("content", "")
            return self._parse_schema(content)
        except Exception as e:
            logger.warning(f"Report decomposition failed: {e}")
            return None

    def _parse_schema(self, content: str) -> Optional[DecomposedSchema]:
        """解析 LLM 返回的 JSON 为 schema。"""
        m = re.search(r'\{[\s\S]*\}', content)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

        # 构建 chapter_structure
        chapters = {}
        for ch in data.get("chapters", []):
            num = ch.get("number", len(chapters) + 1)
            chapters[num] = {
                "number": num,
                "title": ch.get("title", f"第{num}章"),
                "key_points": ch.get("key_points", []),
                "data_needed": ch.get("data_needed", []),
                "rag_query": ch.get("title", ""),
            }

        return DecomposedSchema(
            domain_id=data.get("domain_id", ""),
            display_name=data.get("display_name", ""),
            chapter_structure=chapters,
            scoring_rules=data.get("scoring_rules"),
            data_fields=data.get("data_fields", []),
            style_notes=data.get("style_notes", ""),
            table_templates=data.get("table_templates", []),
        )


# Singleton
report_decomposer = ReportDecomposer()
