"""Stage-aware conversation context manager for the 6-step workflow.

Manages conversation memory with stage-based pruning, Q&A tracking,
and smart compression to keep context focused and within token limits.

Architecture:
    ┌──────────────────────────────────────────────────┐
    │ ContextManager                                    │
    │                                                   │
    │  Stage 1-2 (确认+框架): Light — template + title  │
    │  Stage 3-4 (清单+收集): Full — Q&A pairs + recent │
    │  Stage 5-6 (生成+定稿): Summary — compressed      │
    │                                                   │
    │  Compression: Summarize completed stages into     │
    │  structured JSON summaries, discard raw messages  │
    └──────────────────────────────────────────────────┘
"""

import re
from typing import Dict, List, Any, Optional, Tuple


# ── Token estimation helpers ──

def _estimate_tokens(text: str) -> int:
    """Rough token count for Chinese+English mixed text.

    Chinese: ~1.5 chars per token
    English: ~4 chars per token
    """
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def _estimate_message_tokens(msg: Dict[str, str]) -> int:
    """Estimate tokens in a conversation message."""
    return _estimate_tokens(msg.get("content", ""))


# ── Stage definitions ──

STAGE_CONFIG = {
    1: {  # 接收信息 — user provides bulk input (announcement + local info + images)
        "name": "接收信息",
        "max_recent_messages": 10,
        "keep_summaries": [],
        "include_collected": False,
        "include_needed": False,
    },
    2: {  # 分析填补 — extract data, analyze images, retrieve KB, auto-fill
        "name": "分析填补",
        "max_recent_messages": 16,
        "keep_summaries": [1],
        "include_collected": True,
        "include_needed": True,
    },
    3: {  # 生成报告 — generate AI sections, assemble docx, deliver
        "name": "生成报告",
        "max_recent_messages": 8,
        "keep_summaries": [1, 2],
        "include_collected": True,
        "include_needed": False,
    },
}


class ContextManager:
    """Stage-aware conversation context manager.

    Tracks conversation flow through 3 stages, automatically compresses
    completed stages, and builds optimized prompts for each stage.
    """

    def __init__(self, max_context_tokens: int = 6000):
        self.max_context_tokens = max_context_tokens
        self.reset()

    def reset(self):
        """Reset all context state."""
        # Raw conversation history (for current/active stage)
        self._history: List[Dict[str, str]] = []
        # Stage summaries: {stage_num: compressed_summary_text}
        self._stage_summaries: Dict[int, str] = {}
        # Structured Q&A pairs: [{stage, section, question, answer}]
        self._qa_pairs: List[Dict[str, str]] = []
        # Current workflow stage
        self._current_stage: int = 1
        # Track which topics have been covered (to detect repetition)
        self._covered_topics: set = set()
        # Track filled data snapshot per stage (for progress tracking)
        self._stage_fill_snapshots: Dict[int, Dict[str, Any]] = {}
        # Rollback tracker: {placeholder_key: (attempt_count, last_question_text)}
        self._rollback_tracker: Dict[str, tuple] = {}
        # Track consecutive non-answer responses
        self._consecutive_non_answers: int = 0

    # ── Message Management ──

    def add_user_message(self, content: str) -> None:
        """Add a user message to history."""
        self._history.append({"role": "user", "content": content})
        self._prune_history()

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to history."""
        self._history.append({"role": "assistant", "content": content})
        self._prune_history()

    def _prune_history(self) -> None:
        """Prune raw history based on current stage config."""
        config = STAGE_CONFIG.get(self._current_stage, STAGE_CONFIG[1])
        max_msgs = config["max_recent_messages"]

        if len(self._history) > max_msgs:
            # Keep most recent messages, drop oldest
            self._history = self._history[-max_msgs:]

    # ── Q&A Tracking ──

    def track_qa(self, question: str, answer: str, section: str = "", stage: int = None):
        """Record a question-answer pair for structured tracking.

        This allows the system to know exactly what's been asked and answered,
        preventing duplicate questions.
        """
        stage = stage or self._current_stage
        self._qa_pairs.append({
            "stage": stage,
            "section": section,
            "question": question[:200],
            "answer": answer[:200],
        })
        # Track topic to prevent repetition
        topic = self._extract_topic(question)
        if topic:
            self._covered_topics.add(topic)

    def has_covered_topic(self, topic: str) -> bool:
        """Check if a topic has already been addressed."""
        return topic in self._covered_topics

    def _extract_topic(self, text: str) -> str:
        """Extract the core topic from a question/answer text."""
        # Simple: take first meaningful phrase
        cleaned = re.sub(r'[？?！!。.，,、\s]+', '', text)
        if len(cleaned) > 2:
            return cleaned[:30]
        return cleaned

    # ── Stage Management ──

    def set_stage(self, stage: int):
        """Transition to a new workflow stage."""
        if stage == self._current_stage:
            return

        # Compress previous stage before moving on
        if self._current_stage < stage:
            self._compress_stage(self._current_stage)

        self._current_stage = stage
        # Prune history for new stage
        self._prune_history()

    def _compress_stage(self, stage_num: int):
        """Compress a completed stage into a summary.

        Extracts key information from the stage's conversation and
        stores it as a compact summary, freeing context space.
        """
        if stage_num in self._stage_summaries:
            return  # Already compressed

        # Collect relevant Q&A pairs for this stage
        stage_qas = [qa for qa in self._qa_pairs if qa["stage"] == stage_num]

        if not stage_qas:
            self._stage_summaries[stage_num] = f"阶段{stage_num}已完成（无Q&A记录）"
            return

        # Build compressed summary
        lines = [f"## 阶段{stage_num}摘要（已压缩）"]
        for qa in stage_qas[:10]:  # Max 10 Q&A pairs in summary
            q = qa["question"][:80]
            a = qa["answer"][:80]
            sec = f" [{qa['section']}]" if qa["section"] else ""
            lines.append(f"- Q{sec}: {q}")
            lines.append(f"  A: {a}")

        self._stage_summaries[stage_num] = "\n".join(lines)

    # ── Snapshot Management ──

    def take_snapshot(self, filled_data: dict, placeholders: list):
        """Take a snapshot of fill progress for the current stage."""
        from app.agent.agents.master import MasterAgent
        user_questions = MasterAgent._filter_user_questions(placeholders)
        valid_keys = {p.get("key", "") for p in user_questions}
        filled_count = sum(1 for k in filled_data if k in valid_keys and not k.startswith("_"))
        self._stage_fill_snapshots[self._current_stage] = {
            "filled_count": filled_count,
            "total_placeholders": len(user_questions),
            "filled_keys": list(filled_data.keys()),
            "timestamp": None,
        }

    def get_progress_across_stages(self) -> str:
        """Build a progress summary across all stages."""
        if not self._stage_fill_snapshots:
            return ""

        lines = ["## 各阶段填充进度"]
        for stage_num in sorted(self._stage_fill_snapshots.keys()):
            snap = self._stage_fill_snapshots[stage_num]
            stage_name = STAGE_CONFIG.get(stage_num, {}).get("name", f"阶段{stage_num}")
            lines.append(
                f"- 阶段{stage_num}「{stage_name}」: "
                f"已填{snap['filled_count']}/{snap['total_placeholders']}"
            )
        return "\n".join(lines)

    # ── Context Building ──

    def build_context(
        self,
        state: dict,
        template_context: str = "",
        rag_context: str = "",
    ) -> Dict[str, str]:
        """Build optimized context components for the current stage.

        Returns a dict with keys ready for SYSTEM_PROMPT.format():
            - workflow_stage: Stage description
            - template_context: Template info
            - rag_context: RAG retrieval results
            - collected_summary: What's been collected
            - needed_summary: What still needs collection
            - conversation_recent: Recent conversation
            - stage_summaries: Compressed previous stages
            - qa_context: Structured Q&A pairs
        """
        config = STAGE_CONFIG.get(self._current_stage, STAGE_CONFIG[1])

        return {
            "workflow_stage": self._build_stage_prompt(state),
            "template_context": template_context,
            "rag_context": rag_context,
            "collected_summary": self._build_collected(state) if config["include_collected"] else "",
            "needed_summary": self._build_needed(state) if config["include_needed"] else "",
            "conversation_recent": self._build_recent(),
            "stage_summaries": self._build_stage_summaries(config["keep_summaries"]),
            "qa_context": self._build_qa_context(),
        }

    def build_messages_for_llm(
        self,
        state: dict,
        system_prompt: str,
        template_context: str = "",
        rag_context: str = "",
        max_messages: int = 12,
    ) -> List[Dict[str, str]]:
        """Build the complete message list for LLM call.

        Optimized for the current stage:
        - Stage 1-2: System prompt + recent messages
        - Stage 3-4: System prompt + Q&A summary + recent messages + stage summaries
        - Stage 5-6: System prompt + collected data + compressed history

        Args:
            state: Agent state dict.
            system_prompt: Formatted system prompt.
            template_context: Template info string.
            rag_context: RAG retrieval results.
            max_messages: Max raw messages to include.

        Returns:
            List of message dicts for LLM API.
        """
        messages = []
        config = STAGE_CONFIG.get(self._current_stage, STAGE_CONFIG[1])

        # ---- Build enhanced system prompt ----
        ctx = self.build_context(state, template_context, rag_context)
        enhanced_system = system_prompt

        # Inject stage summaries (compressed history from earlier stages)
        if ctx["stage_summaries"]:
            enhanced_system += f"\n\n## 前序阶段摘要（已压缩）\n{ctx['stage_summaries']}"

        # Inject Q&A context (structured Q&A + covered topics)
        if ctx["qa_context"]:
            enhanced_system += f"\n\n## 已完成的问答记录\n{ctx['qa_context']}"

        messages.append({"role": "system", "content": enhanced_system})

        # ---- Add relevant raw messages ----
        # Only include messages relevant to current stage
        recent = self._history[-max_messages:] if self._history else []
        for msg in recent:
            messages.append(msg)

        return messages

    # ── Internal Builders ──

    def _build_stage_prompt(self, state: dict) -> str:
        """Build stage-specific prompt describing current state and next action."""
        stage = self._current_stage
        filled = state.get("filled_data", {})
        all_placeholders = state.get("template_placeholders", [])
        # Only count user-facing questions (not AI-generated content, not table data)
        from app.agent.agents.master import MasterAgent
        placeholders = MasterAgent._filter_user_questions(all_placeholders)
        total_phs = len(placeholders)
        # Only count fills that correspond to actual placeholder keys (exclude _fixed_* internals)
        valid_keys = {p.get("key", "") for p in placeholders}
        filled_count = sum(1 for k in filled if k in valid_keys and not k.startswith("_"))

        stage_descriptions = {
            1: (
                "📍 **步骤1/6 — 确认报告类型**\n\n"
                "模板已加载。请简短确认后进入步骤2展示框架。\n"
                "• 确认报告名称、业务分类\n"
                "• 从知识库匹配的模板已就绪"
            ),
            2: (
                "📍 **步骤2/6 — 展示报告框架**\n\n"
                "输出模板完整目录（10章），标注每章数据来源：\n"
                "• 第1-3章：需用户提供数据\n"
                "• 第4-9章：系统自动生成（RAG增强）\n"
                "• 第10章：保留模板\n"
                "• 评估机构/人员/资质：内部固定信息，不询问用户\n\n"
                "⏳ 等待用户确认框架后进入步骤3"
            ),
            3: (
                f"📍 **步骤3/6 — 逐个询问待填数据**\n\n"
                f"模板共 **{total_phs}** 个字段，当前已填 **{filled_count}** 个。\n"
                "• 每次只问一个问题，等用户回答后再问下一个\n"
                "• 每个问题说明：需要什么数据、用在哪个章节、是否需上传图片\n"
                "• 按优先级排序：基础信息 → 经济指标 → 实测数据 → 附图 → 政策\n"
                "• **禁止一次性列出所有未填数据**\n\n"
                "⏳ 用户开始提供数据后进入步骤4"
            ),
            4: (
                f"📍 **步骤4/6 — 逐项接收数据并回填**\n\n"
                f"已填：**{filled_count}/{total_phs}** 项。\n"
                f"• 收到数据后简短确认（1句话），立即自然地问下一个问题\n"
                f"• 不要用固定格式模板，用自然的对话推进\n"
                f"• 禁止编造任何估算值，禁止只确认不追问\n"
                f"• 全部数据齐全后告知用户可生成报告"
            ),
            5: (
                "📍 **步骤5/6 — 初稿生成+格式自检**\n\n"
                "必填数据已齐全，可生成初稿。\n"
                "自检项目：\n"
                "1. 目录表格匹配标准模板 ✓\n"
                "2. 无编造数据/政策/群众意见 ✓\n"
                "3. 政策均为现行有效入库文件 ✓\n"
                "4. 风险打分符合DB32/T4013-2021 ✓\n\n"
                "⏳ 告知用户回复「生成报告」输出最终文档"
            ),
            6: (
                "📍 **步骤6/6 — 定稿复核**\n\n"
                "报告已生成。请提醒用户逐项核对：\n"
                "• 业务数据（项目名称、面积、资金等）\n"
                "• 单位名称（责任单位、实施单位）\n"
                "• 数值指标（支持率、评分等）\n"
                "• 文末提供《全章节素材来源明细表》"
            ),
        }

        return stage_descriptions.get(stage, stage_descriptions[1])

    def _build_collected(self, state: dict) -> str:
        """Build a concise summary of collected data, grouped by section."""
        from app.agent.agents.master import MasterAgent
        placeholders = MasterAgent._filter_user_questions(
            state.get("template_placeholders", [])
        )
        filled = state.get("filled_data", {})

        if not placeholders:
            return ""

        # Group by section
        sections: Dict[str, dict] = {}
        for ph in placeholders:
            key = ph.get("key", "")
            section = ph.get("section_title") or "封面/基本信息"
            if section not in sections:
                sections[section] = {"total": 0, "filled": 0, "filled_items": []}
            sections[section]["total"] += 1
            if key in filled and filled[key]:
                display = ph.get("display_name", key)
                display_clean = display.replace("🟡 ", "").replace("📋 ", "").strip()
                sections[section]["filled"] += 1
                sections[section]["filled_items"].append(
                    f"{display_clean[:40]} → {str(filled[key])[:40]}"
                )

        total_filled = sum(s["filled"] for s in sections.values())
        total_phs = sum(s["total"] for s in sections.values())

        lines = [f"**已收集数据**：{total_filled}/{total_phs} 字段"]

        # Show filled items (compact)
        for section_name, data in sections.items():
            if data["filled_items"]:
                icon = "✅" if data["filled"] == data["total"] else "🔄"
                lines.append(f"\n{icon} {section_name} ({data['filled']}/{data['total']})")
                for item in data["filled_items"][:3]:
                    lines.append(f"  ✓ {item}")
                if len(data["filled_items"]) > 3:
                    lines.append(f"  ...及其他{len(data['filled_items']) - 3}项")

        # Include generated analysis
        generated = state.get("generated_sections", {})
        if generated:
            gen_labels = {
                "legality": "合法性分析", "rationality": "合理性分析",
                "feasibility": "可行性分析", "controllability": "可控性分析",
                "risk_scores": "风险评分", "risk_factor_table": "风险因素表",
            }
            done = [label for key, label in gen_labels.items() if key in generated]
            if done:
                lines.append(f"\n📊 已生成分析：{'、'.join(done)}")

        return "\n".join(lines)

    def _build_needed(self, state: dict) -> str:
        """Build prioritized list of still-needed data, grouped by 6 categories."""
        placeholders = state.get("template_placeholders", [])
        filled = state.get("filled_data", {})
        skipped = set(state.get("collecting_skipped_keys", []))

        if not placeholders:
            return ""

        # 6 data categories with keywords
        CATEGORIES = {
            "📋 基础项目信息": ["项目名称", "责任单位", "实施单位", "决策名称", "报告标题",
                           "单位名称", "负责人", "联系人", "联系电话", "位置", "地址"],
            "💰 经济指标": ["投资", "金额", "资金", "补偿", "费用", "亩", "面积", "公顷", "万元"],
            "📊 现场实测数据": ["调查", "问卷", "统计", "支持率", "反对", "户数", "人数", "座谈"],
            "📜 政策文件信息": ["文号", "审批", "批复", "规划", "法规", "DB32", "条例"],
            "🖼️ 附图附表参数": ["图", "照片", "图片", "红线", "公示", "影像", "附件", "附图"],
            "🏢 单位资质资料": ["营业执照", "资质", "证书", "许可证", "资格"],
        }

        # Categorize unfilled
        categorized = {cat: [] for cat in CATEGORIES}
        uncategorized = []

        for ph in placeholders:
            key = ph.get("key", "")
            if key in filled or key in skipped:
                continue
            display = ph.get("display_name", "")
            if not display or display.startswith("🟡 图"):
                continue

            display_clean = display.replace("🟡 ", "").replace("📋 ", "").strip()
            section = ph.get("section_title") or ""
            section_ref = f" ({section})" if section else ""
            item = f"`{key}`: {display_clean[:60]}{section_ref}"

            matched = False
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    categorized[cat].append(item)
                    matched = True
                    break
            if not matched:
                uncategorized.append(item)

        total_unfilled = sum(len(v) for v in categorized.values()) + len(uncategorized)
        if total_unfilled == 0:
            # Check if analyses are pending
            generated = state.get("generated_sections", {})
            if "legality" not in generated:
                return "**下一步**：基础信息已齐全，需生成决策综合分析（调用RationalityAgent）"
            if "risk_scores" not in generated:
                return "**下一步**：分析内容已生成，需进行风险等级评分（调用RiskScorer）"
            return "**✅ 所有数据已齐全**，提醒用户回复「生成报告」即可"

        lines = [f"**待收集数据**（{total_unfilled}项，按6大类）：\n"]

        priority_order = list(CATEGORIES.keys())
        for cat in priority_order:
            items = categorized[cat]
            if not items:
                continue
            lines.append(f"{cat}（{len(items)}项）：")
            for item in items[:5]:
                lines.append(f"  - {item}")
            if len(items) > 5:
                lines.append(f"  ...及其他{len(items) - 5}项")
            lines.append("")

        if uncategorized:
            lines.append(f"📌 其他（{len(uncategorized)}项）")
            for item in uncategorized[:3]:
                lines.append(f"  - {item}")

        # Add covered topics note
        if self._covered_topics:
            recent_topics = list(self._covered_topics)[-5:]
            lines.append(f"\n⚠️ 已询问过的话题：{'、'.join(recent_topics)}")
            lines.append("请勿重复询问以上话题。")

        # One-by-one questioning directive
        lines.append("\n🔑 **核心规则：逐个询问，每次只问一项**")
        lines.append("从优先级最高的未填项中选一项，用自然语言询问用户。")
        lines.append("格式：先说明这是什么数据、用在哪个章节，再提问。需要图片时明确说「请上传XX图片」。")
        lines.append("🔴 **连贯追问铁律**：每条回复 = 确认上一项（✅已收到XX，进度X/Y）+ 分隔线 + 询问下一项。")
        lines.append("严禁只确认不追问！用户回答后立即在同一回复中追问下一项，不要停下来！")

        return "\n".join(lines)

    def _build_recent(self) -> str:
        """Build a compact view of recent conversation."""
        if not self._history:
            return ""

        # Get last 4 user messages
        user_msgs = [
            h["content"][:150]
            for h in self._history
            if h.get("role") == "user"
        ][-4:]

        if not user_msgs:
            return ""

        lines = ["**用户最近发言**："]
        for i, msg in enumerate(user_msgs, 1):
            lines.append(f"{i}. {msg}")

        return "\n".join(lines)

    def _build_stage_summaries(self, keep_stages: List[int]) -> str:
        """Build compressed summaries for specified stages."""
        summaries = []
        for stage_num in keep_stages:
            if stage_num in self._stage_summaries:
                summaries.append(self._stage_summaries[stage_num])

        if not summaries:
            return ""

        return "\n\n".join(summaries)

    def _build_qa_context(self) -> str:
        """Build structured Q&A context for the current stage."""
        if not self._qa_pairs:
            return ""

        # Filter Q&A pairs relevant to current stage
        stage_qas = [
            qa for qa in self._qa_pairs
            if qa["stage"] >= self._current_stage - 1  # Current + previous stage
        ]

        if not stage_qas:
            return ""

        lines = ["**已完成的问答**（按时间顺序）："]
        for qa in stage_qas[-8:]:  # Last 8 Q&A pairs
            q = qa["question"][:60]
            a = qa["answer"][:60]
            lines.append(f"- 问：{q}")
            lines.append(f"  答：{a}")

        return "\n".join(lines)

    # ── Rollback & Recovery ──

    def track_rollback(self, placeholder_key: str, question_text: str) -> int:
        """Track a question that may need rollback. Returns attempt count (1, 2, 3...)."""
        if placeholder_key in self._rollback_tracker:
            count, _ = self._rollback_tracker[placeholder_key]
            count += 1
        else:
            count = 1
        self._rollback_tracker[placeholder_key] = (count, question_text[:200])
        return count

    def should_rollback(self, placeholder_key: str) -> bool:
        """Check if a question has been asked too many times without a good answer."""
        if placeholder_key in self._rollback_tracker:
            count, _ = self._rollback_tracker[placeholder_key]
            return count >= 2  # Rollback after 2 failed attempts
        return False

    def get_rollback_hint(self, placeholder_key: str) -> str:
        """Get a rephrased question hint when original question failed."""
        if placeholder_key in self._rollback_tracker:
            count, _ = self._rollback_tracker[placeholder_key]
            hints = {
                2: "（换个方式问：您能提供一个大致的范围或参考值吗？）",
                3: "（如果暂时无法确定，可以回复「跳过」先留空，后续再补充）",
            }
            return hints.get(count, "（可以回复「跳过」暂时留空此项）")
        return ""

    def track_user_response(self, user_input: str) -> bool:
        """Check if user response is a non-answer. Returns True if it's a valid answer."""
        stripped = user_input.strip()
        # Short responses that are clearly non-answers
        non_answer_exact = {"不知道", "不清楚", "？", "?", "继续", "下一步",
                            "然后", "接下来", "跳过", "再说", "等等", "稍等",
                            "没有", "没", "无"}
        if stripped in non_answer_exact:
            self._consecutive_non_answers += 1
            return False
        if len(stripped) < 3:
            self._consecutive_non_answers += 1
            return False

        # Valid answer — reset counter
        self._consecutive_non_answers = 0
        return True

    def is_stuck(self) -> bool:
        """Check if the conversation appears stuck (consecutive non-answers >= 3)."""
        return self._consecutive_non_answers >= 3

    def get_stuck_recovery_message(self) -> str:
        """Get a recovery message when conversation is stuck."""
        return (
            "😅 看起来前面的问题可能不太清楚，我换一种方式帮您梳理。\n\n"
            "如果某些数据暂时无法确定，您可以直接回复「**跳过**」，"
            "我会在报告中标注【待用户补充】，后续您可以随时回来补充。\n\n"
            "我们继续吧——"
        )

    # ── Diagnostics ──

    def get_stats(self) -> Dict[str, Any]:
        """Get context manager statistics for debugging."""
        total_tokens = sum(_estimate_message_tokens(m) for m in self._history)
        return {
            "current_stage": self._current_stage,
            "stage_name": STAGE_CONFIG.get(self._current_stage, {}).get("name", "未知"),
            "raw_messages": len(self._history),
            "estimated_tokens": total_tokens,
            "qa_pairs": len(self._qa_pairs),
            "covered_topics": len(self._covered_topics),
            "stage_summaries": list(self._stage_summaries.keys()),
            "max_context_tokens": self.max_context_tokens,
            "usage_percent": int(total_tokens / self.max_context_tokens * 100) if self.max_context_tokens else 0,
        }


# ── Singleton for convenience ──

_context_manager: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    """Get or create the global context manager."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager


def reset_context_manager():
    """Reset the global context manager (e.g., for new session)."""
    global _context_manager
    if _context_manager:
        _context_manager.reset()
    else:
        _context_manager = ContextManager()
