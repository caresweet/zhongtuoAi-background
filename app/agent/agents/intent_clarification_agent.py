"""IntentClarificationAgent — LLM semantic intent analysis and anti-hallucination guard.

Analyzes user input to determine intent: generation_request, question, data_provision, etc.
Returns structured intent result with confidence score and hallucination risk assessment.
"""

import asyncio
import json
import logging
import re
from typing import Dict, List, Any, Optional

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

INTENT_DEFINITIONS = {
    "generation_request": "User wants to generate a report or chapter",
    "question": "User is asking a question about regulations, process, or knowledge",
    "data_provision": "User is providing project data (location, area, document numbers, etc.)",
    "revision_request": "User wants to revise existing content",
    "confirmation": "User is confirming or approving something",
    "chapter_feedback": "User is giving feedback on a generated chapter",
    "progress_check": "User is checking generation progress",
    "greeting": "User is greeting or chatting casually",
    "complaint": "User is complaining about something",
    "file_upload": "User is uploading a file",
    "table_construction": "User wants to build a table",
    "kb_explanation": "User wants a knowledge base explanation",
    "bidding_generation": "User wants to generate bidding documents",
    "mixed": "User has multiple intents",
    "unknown": "Cannot determine intent",
}


class IntentClarificationAgent(BaseAgent):
    """LLM-based intent classifier with hallucination guard."""

    name = "IntentClarificationAgent"
    description = "Analyze user input to determine intent and detect hallucination risk"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        return {"summary": "Analyzing user intent...", "steps": [], "actions": []}

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "completed"}

    async def analyze_intent(self, state: dict, user_input: str) -> Optional[Dict[str, Any]]:
        """Analyze user intent with keyword matching + basic heuristics."""
        if not user_input or not user_input.strip():
            return {"primary_intent": "unknown", "confidence": 0, "needs_clarification": False}

        inp = user_input.strip()

        # Keyword-based intent classification
        gen_keywords = ["生成报告", "开始生成", "逐章生成", "写报告", "做报告", "编写报告", "编制报告"]
        question_keywords = ["是什么", "怎么", "如何", "规定", "标准", "法规", "要求", "？", "哪些", "多少"]
        data_keywords = ["位置", "面积", "文号", "户数", "金额", "亩", "平方米", "元/亩", "坐落"]
        greeting_keywords = ["你好", "您好", "hi", "hello", "在吗"]
        complaint_keywords = ["不好", "不行", "太差", "错误", "不对", "有问题", "重写"]

        # Check for generation request
        if any(kw in inp for kw in gen_keywords):
            return {"primary_intent": "generation_request", "confidence": 90, "needs_clarification": False, "hallucination_risk": None}

        # Check for bidding context
        if any(kw in inp for kw in ["招标", "投标", "评标", "中标"]):
            return {"primary_intent": "bidding_generation", "confidence": 85, "needs_clarification": False, "hallucination_risk": None}

        # Check for data provision
        if any(kw in inp for kw in data_keywords):
            return {"primary_intent": "data_provision", "confidence": 75, "needs_clarification": False, "extracted_data": {}, "hallucination_risk": None}

        # Check for question
        if any(kw in inp for kw in question_keywords):
            return {"primary_intent": "question", "confidence": 70, "needs_clarification": False, "hallucination_risk": None}

        # Check for greeting
        if inp in greeting_keywords or len(inp) < 10:
            return {"primary_intent": "greeting", "confidence": 95, "needs_clarification": False, "hallucination_risk": None}

        # Check for complaint
        if any(kw in inp for kw in complaint_keywords):
            return {"primary_intent": "complaint", "confidence": 80, "needs_clarification": False, "hallucination_risk": None}

        # Detect hallucination risk — user asks for data we don't have
        if any(kw in inp for kw in ["编一个", "随便写", "差不多", "随便填", "大概写"]):
            return {
                "primary_intent": "data_provision",
                "confidence": 50,
                "needs_clarification": True,
                "clarification_question": "请提供准确的项目数据，系统不会编造信息。",
                "hallucination_risk": "用户要求编造数据，需拒绝并引导提供真实数据",
            }

        # Default: treat as data_provision or chat
        return {"primary_intent": "data_provision", "confidence": 40, "needs_clarification": False, "extracted_data": {}, "hallucination_risk": None}
