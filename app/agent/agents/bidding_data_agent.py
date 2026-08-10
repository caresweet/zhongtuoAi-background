"""BiddingDataAgent — extracts structured data from bidding documents."""

import re
import logging
from typing import Dict, Any, Optional
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class BiddingDataAgent(BaseAgent):
    """Extract structured bidding data from uploaded documents."""

    name = "BiddingDataAgent"
    description = "Parse bidding documents and extract structured data"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        return {"summary": "Extracting bidding data...", "steps": [], "actions": []}

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        # Extract data from PDF texts
        pdf_texts = state.get("_pdf_texts", {})
        all_text = "\n".join(str(v) for v in pdf_texts.values()) if pdf_texts else ""

        data = self._regex_extract(all_text)
        if data:
            state.setdefault("_bidding_data", {}).update(
                {k: v for k, v in data.items() if v and str(v).strip()}
            )
        return {"status": "completed", "fields_extracted": len(data)}

    def _regex_extract(self, text: str) -> Dict[str, str]:
        """Extract bidding fields using regex."""
        data = {}
        patterns = [
            (r'(?:项目名称|采购项目名称)[：:]\s*(\S.{2,80}?)(?:\n|。|，)', "bid_project_name"),
            (r'(?:项目编号|招标编号|采购编号)[：:]\s*(\S{5,40})', "bid_reference"),
            (r'(?:招标人|采购人|采购单位)[：:]\s*(\S{2,40})', "bid_owner"),
            (r'(?:预算|最高限价)[：:]\s*(\d[\d,.]*\s*(?:万元|元))', "bid_budget"),
            (r'(?:截止时间|递交截止)[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}.*?\d{1,2}:\d{2})', "bid_deadline"),
        ]
        for pattern, key in patterns:
            m = re.search(pattern, text)
            if m:
                data[key] = m.group(1).strip()
        return data

    async def _llm_deep_extract(self, text: str) -> Dict[str, str]:
        """Deep extraction using LLM (stub)."""
        return {}
