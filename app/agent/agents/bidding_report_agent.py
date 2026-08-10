"""BiddingReportAgent — generates bidding/tender reports."""

import logging
from typing import Dict, Any
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


# Module-level constants for domain registry
REPORT_TYPES = {
    "tender_announcement": "招标公告",
    "bid_evaluation": "评标报告",
    "bid_result": "中标公示",
    "tender_response": "投标文件",
    "tender_summary": "招标情况报告",
}

REPORT_CHAPTERS = {
    "tender_announcement": ["项目概况", "招标范围", "投标人资格要求", "招标文件获取", "投标文件递交"],
    "bid_evaluation": ["评标工作概述", "评标委员会组成", "评标方法和标准", "投标文件评审", "评标结果"],
    "bid_result": ["招标项目基本情况", "中标候选人公示", "中标结果", "异议与投诉"],
    "tender_response": ["投标函", "报价明细", "技术方案", "服务承诺", "资质证明"],
    "tender_summary": ["项目概况", "招标过程", "投标情况", "评标情况", "定标结果"],
}


class BiddingReportAgent(BaseAgent):
    """Generate bidding documents (announcements, evaluation reports, etc.)."""

    name = "BiddingReportAgent"
    description = "Generate structured bidding documents from project data"
    covered_steps = []

    # Class-level copies for backward compatibility
    REPORT_TYPES = REPORT_TYPES
    REPORT_CHAPTERS = REPORT_CHAPTERS

    async def think(self, state: dict) -> Dict[str, Any]:
        return {"summary": "Preparing bidding report...", "steps": [], "actions": []}

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "completed"}

    def _detect_report_type(self, user_input: str) -> str:
        """Detect what type of bidding report to generate."""
        if any(kw in (user_input or "") for kw in ["投标文件", "响应文件", "投标", "响应"]):
            return "tender_response"
        if any(kw in (user_input or "") for kw in ["招标公告", "公告"]):
            return "tender_announcement"
        if any(kw in (user_input or "") for kw in ["评标报告", "评标"]):
            return "bid_evaluation"
        if any(kw in (user_input or "") for kw in ["中标公示", "中标"]):
            return "bid_result"
        return "tender_response"
