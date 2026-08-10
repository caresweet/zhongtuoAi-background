"""TenderSpecAnalyzer — parse a tender/negotiation standard document into a spec
that drives bid-document generation.

Given the采购方's《招标/磋商标准文件》, the LLM extracts:
1. project basics (name, code, buyer, agency, budget, service scope, deadlines)
2. required response modules (what the bid document must contain)
3. qualification documents required (which company assets to include)
4. scoring criteria (esp. the technical-proposal focus and its sub-points)

The result is a structured `tender_spec` dict that the bidding orchestrator uses
to decide which modules to generate and what the technical proposal must cover.
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

_ANALYZE_PROMPT = """你是招投标专家。下面是采购方发布的《招标/竞争性磋商标准文件》正文。请解析出供应商编写投标（响应）文件所需的关键信息，用于指导投标文件生成。

请输出 JSON 对象：
{{
  "project": {{
    "name": "项目名称", "code": "项目编号", "buyer": "采购人",
    "agency": "代理机构", "budget": "预算金额/最高限价",
    "purchase_method": "采购方式", "scope": "采购需求/服务内容概述",
    "duration": "合同履行期限", "deadline": "响应文件递交截止时间"
  }},
  "required_modules": ["投标文件必须包含的模块，如 磋商函/响应函、资格证明文件、人员配备表、技术方案 等"],
  "qualification_docs": ["资质审查须提交的资格证明文件清单，如 营业执照、财务报告、社保纳税证明、法人证明/授权委托、承诺函、测绘资质证书、信用记录 等"],
  "personnel_requirements": "对项目团队人员的要求（人数、职称、资格证书等）",
  "scoring": [
    {{"item": "评分项名称", "score": 分值数字, "focus": "评审要点/得分说明"}}
  ],
  "technical_focus": ["技术方案应重点覆盖的内容（依据评分标准与采购需求归纳）"]
}}

只输出 JSON 对象，不要额外文字。缺失的字段留空字符串或空数组。

【招标标准文件正文】
{doc_text}"""


class TenderSpecAnalyzer:
    def __init__(self, llm_service=None):
        if llm_service is None:
            from app.services.llm_service import llm_service as _llm
            llm_service = _llm
        self._llm = llm_service

    async def analyze(self, doc_text: str) -> Dict[str, Any]:
        """Parse the tender standard doc into a structured spec."""
        empty = self._empty_spec()
        if not doc_text or len(doc_text.strip()) < 100:
            return empty
        if not self._llm or not self._llm.is_available:
            return empty

        prompt = _ANALYZE_PROMPT.format(doc_text=doc_text[:30000])
        try:
            resp = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                temperature=0.1,
            )
        except Exception as e:
            logger.error(f"Tender spec analysis failed: {e}")
            return empty

        spec = self._parse_json_obj(resp)
        return spec or empty

    def _parse_json_obj(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                # normalize
                data.setdefault("project", {})
                data.setdefault("required_modules", [])
                data.setdefault("qualification_docs", [])
                data.setdefault("scoring", [])
                data.setdefault("technical_focus", [])
                data.setdefault("personnel_requirements", "")
                return data
        except json.JSONDecodeError as e:
            logger.warning(f"Tender spec JSON parse failed: {e}")
        return None

    @staticmethod
    def _empty_spec() -> Dict[str, Any]:
        return {
            "project": {}, "required_modules": [], "qualification_docs": [],
            "personnel_requirements": "", "scoring": [], "technical_focus": [],
        }


tender_spec_analyzer = TenderSpecAnalyzer()
