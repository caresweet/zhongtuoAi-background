"""联网爬取 — 用 LLM 联网搜索（enable_search）获取同类型优秀报告的结构化要点。

合规铁律：只搜政府公开政策、公开范文、公开书籍；搜索不到就停，绝不爬违规网站。
不返回报告原文（避免版权问题），而是返回 LLM 提炼的"结构 + 措辞 + 评分规则"骨架。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CrawledReport:
    """联网搜索到的一篇优秀报告的提炼结果。"""
    title: str = ""
    report_type: str = ""          # 报告类型
    region: str = ""               # 地区
    chapter_structure: List[str] = field(default_factory=list)  # 章节标题
    key_points: str = ""           # 核心要点（LLM 提炼）
    scoring_rules: str = ""        # 评分规则（如有）
    source_note: str = ""          # 来源说明（合规公开来源）


class WebCrawler:
    """联网搜索同类型优秀报告，返回结构化提炼（非原文）。"""

    async def crawl(
        self,
        report_type: str,
        region: str = "",
        llm_service=None,
        max_results: int = 3,
    ) -> List[CrawledReport]:
        """联网搜索同类型优秀报告。

        Args:
            report_type: 报告类型（如"可行性研究报告"）
            region: 地区（如"江苏省"，留空则全国）
            llm_service: LLM 服务（需支持 enable_search）
            max_results: 最多返回几篇

        Returns:
            List[CrawledReport] — 提炼后的报告骨架，非原文
        """
        if not llm_service:
            logger.warning("No LLM service for web crawling")
            return []

        region_part = f"，重点 {region} 地区" if region else ""
        prompt = f"""你是报告结构分析专家。请联网搜索「{report_type}」{region_part}的公开优秀范文和相关政策依据。

## 合规要求（严格遵守）
- 只参考政府公开政策、公开学术文献、公开范文
- 不爬取违规网站、付费内容、内部文件
- 搜索不到就明确说明"未找到"，不要编造

## 提炼要求
针对「{report_type}」，提炼出：
1. **标准章节结构**：这类报告通常分几章、每章标题和核心内容
2. **评分/评价规则**：如果这类报告有量化评分标准，列出指标和权重
3. **需要收集的资料**：写这类报告通常需要用户提供哪些数据/资料
4. **专业措辞特征**：这类报告的公文风格、常用句式

## 输出 JSON 格式（最多 {max_results} 篇范文的提炼）
```json
{{
  "reports": [
    {{
      "title": "范文标题",
      "region": "地区",
      "chapter_structure": ["第一章 标题", "第二章 标题", ...],
      "key_points": "核心要点摘要",
      "scoring_rules": "评分规则（无则空字符串）",
      "source_note": "来源说明"
    }}
  ],
  "note": "搜索情况说明"
}}
```"""

        try:
            result = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000, temperature=0.2, enable_search=True,
            )
            content = str(result) if result else ""
            return self._parse_result(content)
        except Exception as e:
            logger.warning(f"Web crawl failed: {e}")
            return []

    def _parse_result(self, content: str) -> List[CrawledReport]:
        """解析 LLM 返回的 JSON。"""
        import re
        m = re.search(r'\{[\s\S]*\}', content)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

        reports = []
        for r in data.get("reports", []):
            reports.append(CrawledReport(
                title=r.get("title", ""),
                region=r.get("region", ""),
                chapter_structure=r.get("chapter_structure", []),
                key_points=r.get("key_points", ""),
                scoring_rules=r.get("scoring_rules", ""),
                source_note=r.get("source_note", ""),
            ))
        return reports


# Singleton
web_crawler = WebCrawler()
