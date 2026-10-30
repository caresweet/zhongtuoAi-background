"""BiddingChapterAgent — 逐模块生成投标/磋商文件正文。

业主口径（2026-10-30）：「部分内容不需要动的就不动，需要 AI 去做的就让 agent
根据**模板**和**知识库**上的内容拟人构写」。所以这里的分工是：

- `source != "llm"`（资格证明、人员配备这类）→ **不写正文**，由资料库/人员库直接取用；
- `source == "llm"`（技术方案各模块、磋商函等）→ 由本 agent 撰写：
    项目事实（`_bidding_data` + 用户资料）
  + 模板结构（该地区投标文件模板的标题大纲，只作结构参考，不抄内容）
  + 知识库素材（bidding 域 RAG 检索）
  → 拟人化的专业文字。

🔴 以前这里是 30 行的占位实现，`act()` 永远返回「【待生成】」，
   而 `master._replace_tech_proposal_with_ai` 会拿它**替换掉模板里的技术方案原文**——
   等于把好好的模板正文换成一堆「【待生成】」。现在补成真写。
"""

import logging
from typing import Any, Dict, Optional

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

# 项目字段 → 中文标注（写进提示词，让模型知道每个值是什么）
_BIDDING_FIELD_LABELS = {
    "bid_project_name": "项目名称",
    "bid_reference": "项目编号",
    "bid_owner": "采购人（招标人）",
    "bid_agency": "代理机构",
    "bid_budget": "预算金额",
    "bid_deadline": "投标截止时间",
    "bid_open_time": "开标时间",
    "bid_location": "项目地点",
    "bid_scope": "采购内容",
    "bid_duration": "服务期限",
}

# 生成出来全是这些字样 → 视为没写（调用方据此保留模板原文）
_PLACEHOLDER_MARKS = ("【待生成】", "【待补充】", "待生成", "TODO")

_SYSTEM_PROMPT = (
    "你是招标投标文件编写专家，替供应商写投标（磋商）响应文件。\n"
    "写作要求：\n"
    "- 说人话：短句为主，一句话只讲一件事；不用「为……提供有力保障」这类公文套话；\n"
    "- 具体：能写数量、时限、责任岗位、做法步骤就写具体，不写「加强」「提升」了事；\n"
    "- 不编造：项目数据只用给定的事实；没有的数据写做法与标准，不臆造数字；\n"
    "- 结构清楚：用 `##` 作模块标题、`###` 作小节标题，小节下 2～4 段实质内容；\n"
    "- 表格用 markdown 语法，只在确有数据支撑时使用。\n"
)


class BiddingChapterAgent(BaseAgent):
    """Generate a single chapter/module of a bidding document."""

    name = "BiddingChapterAgent"
    description = "Generate individual bidding document modules"
    covered_steps = []

    def __init__(self, llm_service=None, report_type: str = "tender_response",
                 chapter_index: int = 0, chapter_def: Optional[Dict] = None):
        super().__init__(llm_service)
        self.report_type = report_type
        self.chapter_index = chapter_index
        self.chapter_def = chapter_def or {}

    async def think(self, state: dict) -> Dict[str, Any]:
        title = self.chapter_def.get("title", f"模块{self.chapter_index}")
        return {"summary": f"Generating bidding module: {title}", "steps": [], "actions": []}

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        title = self.chapter_def.get("title", "")
        source = (self.chapter_def.get("source") or "llm").strip()

        # 资料/人员类模块不是「写」出来的 —— 交回调用方按资产库取用
        if source != "llm" or self._llm is None:
            return {"status": "skipped", "markdown": f"## {title}\n\n【待生成】\n"}

        try:
            markdown = await self._write_module(state, title)
        except Exception as e:
            logger.warning(f"[投标] 模块「{title}」生成失败：{e}")
            return {"status": "failed", "markdown": f"## {title}\n\n【待生成】\n"}

        return {"status": "completed", "markdown": markdown}

    # ── 内部 ──
    async def _write_module(self, state: dict, title: str) -> str:
        facts = self._fact_lines(state)
        rag_text = await self._rag_text(state, title)
        outline = await self._template_outline(state)

        key_points = self.chapter_def.get("key_points") or []
        desc = self.chapter_def.get("description") or ""

        user = (
            f"请撰写投标（磋商）响应文件的模块「{title}」。\n\n"
            f"## 模块要求\n{desc}\n"
            + ("".join(f"- {p}\n" for p in key_points))
            + f"\n## 项目事实（只能用这些，不得另编）\n{facts or '（未提取到项目信息，写做法时不要写具体数字）'}\n"
            + (f"\n## 知识库素材（法规/标准/同类做法，仅供参考）\n{rag_text}\n" if rag_text else "")
            + (f"\n## 模板结构参考（该地区投标文件的目录结构，只借结构不抄内容）\n{outline}\n" if outline else "")
            + f"\n输出要求：从 `## {title}` 开始，只输出正文 markdown，不要解释。"
        )

        resp = await self._llm.chat(
            messages=[{"role": "user", "content": user}],
            system=_SYSTEM_PROMPT,
            max_tokens=4000,
            temperature=0.35,
        )
        text = (resp or "").strip()
        if not text or any(m in text for m in _PLACEHOLDER_MARKS):
            raise ValueError("模型未返回有效正文")
        if not text.lstrip().startswith("##"):
            text = f"## {title}\n\n{text}"
        return text + "\n"

    @staticmethod
    def _fact_lines(state: dict) -> str:
        data = state.get("_bidding_data") or {}
        lines = []
        for k, label in _BIDDING_FIELD_LABELS.items():
            v = str(data.get(k, "") or "").strip()
            if v:
                lines.append(f"- {label}：{v}")
        # 用户上传的资料（文件名即线索，正文据此写做法）
        for f in (state.get("_uploaded_files") or [])[:20]:
            name = f.get("original_name") if isinstance(f, dict) else str(f)
            if name:
                lines.append(f"- 项目资料：{name}")
        return "\n".join(lines)

    async def _rag_text(self, state: dict, title: str) -> str:
        try:
            from app.rag.rag_service import rag_service
            q = (self.chapter_def.get("rag_query") or title).strip()
            items = await rag_service.retrieve_hybrid(
                q, state.get("session_id", ""), n_results=8, domain="bidding",
            )
        except Exception as e:
            logger.warning(f"[投标] 知识库检索失败（不影响写作）：{e}")
            return ""
        out = []
        for it in (items or [])[:8]:
            doc = str(it.get("document") or it.get("text") or "").strip()
            if not doc:
                continue
            out.append("- " + doc[:400].replace("\n", " "))
        return "\n".join(out)

    async def _template_outline(self, state: dict) -> str:
        from app.services.template_chapter_chunker import outline_headings
        path = state.get("template_path") or ""
        if not path:
            try:
                from app.services.template_selector import (
                    resolve_template_path, build_match_text,
                )
                data = state.get("_bidding_data") or {}
                path = await resolve_template_path(
                    "bidding",
                    build_match_text(data.get("bid_project_name"), data.get("bid_reference")),
                )
                if path:
                    state["template_path"] = path
            except Exception:
                path = ""
        if not path:
            return ""
        # 大纲只取一层+二层，避免把 38MB 投标文件的全部小节都塞进提示词
        return "\n".join(outline_headings(path, limit=60).splitlines()[:60])
