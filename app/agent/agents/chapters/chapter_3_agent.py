"""Chapter3Agent — 决策事项基本情况。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter3Agent(ChapterAgentBase):
    name = "Chapter3Agent"
    description = "生成第3章：决策事项基本情况"
    covered_steps = [3]
    chapter_number = 3
    chapter_title = "决策事项基本情况"
    rag_query_extra = "决策事项基本情况 决策名称 地理位置 面积 地类 资金筹措 实施周期"
    required_data_keys = ["project_name", "location"]
    key_tables = ["表3-1 土地分类面积表"]


    @staticmethod
    def _build_land_table_data(state: dict) -> str:
        """构建勘测定界「土地分类面积表」的注入文本（表3-1 唯一数据源）。

        Args:
            state: 含 `_extracted_tables`（或 `_project_material_facts._extracted_tables`）的状态。

        Returns:
            可直接拼进 prompt 的文本；无表时返回空串。
        """
        filled = (state or {}).get("filled_data", {}) or {}
        tables = ((state or {}).get("_extracted_tables")
                  or filled.get("_extracted_tables")
                  or ((state or {}).get("_project_material_facts", {}) or {}).get("_extracted_tables")
                  or [])
        if not tables:
            return ""
        parts = ["## 📐 勘测定界表（表3-1 的唯一数据源）",
                 "🔴 （五）节的地类构成、各权属单位面积**必须且只能**引用下表数值，"
                 "禁止把分项填成总面积，禁止编造。"]
        for t in tables[:3]:
            if not isinstance(t, dict):
                continue
            if t.get("raw_markdown"):
                parts.append(str(t["raw_markdown"]))
            elif t.get("headers"):
                parts.append("表头：" + " | ".join(str(h) for h in t["headers"]))
                for row in (t.get("rows") or [])[:12]:
                    parts.append(" | ".join(str(c) for c in row))
        return "\n".join(parts)

    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """注入勘测定界「土地分类面积表」（表3-1 唯一数据源）。"""
        return self._build_land_table_data(state)
