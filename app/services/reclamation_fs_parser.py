# -*- coding: utf-8 -*-
"""可研报告解析 —— 从主体项目可行性研究报告抽复垦需要的基础字段。

仓库里没有现成的可研解析器（设计文档 §12.3）。骨架照
`tender_spec_analyzer.TenderSpecAnalyzer`：整篇喂 LLM + JSON schema + 容错解析。
**不新建一套抽取框架**，只做复垦这一件事。
"""

import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

_SCHEMA_PROMPT = """你是土地复垦方案编制助手。下面是一份主体项目的可行性研究报告正文。
请抽取以下字段，**只输出 JSON**，不要解释。

字段（找不到就置 null，**不要编造**）：
- project_name   项目名称
- build_unit     建设单位
- location       建设地点（写到镇/村一级）
- build_content  建设内容（一到两句话）
- build_period   建设周期（起止时间）
- process        施工工序（尤其是有没有表土剥离、垫层/混凝土层的厚度）

正文：
---
{text}
---
"""

_FIELDS = ("project_name", "build_unit", "location",
           "build_content", "build_period", "process")


class ReclamationFsParser:
    """可研报告 → 结构化字段。"""

    def __init__(self, llm_service=None):
        if llm_service is None:
            from app.services.llm_service import llm_service as _llm
            llm_service = _llm
        self._llm = llm_service

    async def parse(self, doc_text: str) -> Dict[str, Any]:
        text = (doc_text or "").strip()
        # 过短不值得调用模型 —— 门限与 tender_spec_analyzer 保持一致
        if len(text) < 100:
            return {}
        if not self._llm or not getattr(self._llm, "is_available", True):
            return {}

        prompt = _SCHEMA_PROMPT.format(text=text[:20000])
        # 🔴 真实接口收的是 **messages 列表**，不是裸字符串 ——
        #    见 `LLMService.chat(messages, system=None, max_tokens=…, temperature=…)`
        #    与参照物 tender_spec_analyzer.analyze() 的写法。
        try:
            raw = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                temperature=0.1,
            )
        except Exception as e:
            # 🔴 **传输失败也要容错** —— 与参照物 tender_spec_analyzer.analyze() 一致。
            #    本模块的定位是「抽不到就少几个字段」，不是「抽不到就崩」：
            #    模型侧偶发故障不该把整份报告的生成打断。
            logger.error("可研解析失败（已降级为空结果）：%s", e)
            return {}
        data = self._parse_json_obj(raw)
        # 只保留已知字段；缺失的不给键（不编造）
        return {k: data[k] for k in _FIELDS if data.get(k)}

    @staticmethod
    def _parse_json_obj(raw: str) -> Dict[str, Any]:
        """从模型回复里抠 JSON 对象；失败返回 {}（不抛）。"""
        if not raw:
            return {}
        # 优先取 ```json … ``` 代码块（正则与 tender_spec_analyzer._parse_json_obj 同款）
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        text = m.group(1) if m else raw
        i, j = text.find("{"), text.rfind("}")
        if i == -1 or j <= i:
            return {}
        try:
            obj = json.loads(text[i:j + 1])
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}
