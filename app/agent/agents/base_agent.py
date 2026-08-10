"""BaseAgent — abstract base class for all report-generation agents.

Each agent follows the think → act → validate pattern:
1. think(state)  — analyze current state, produce a plan (visible to user)
2. act(state, plan) — execute the plan, produce results
3. validate(result) — self-check output for quality

Agents communicate through the shared AgentState dict. They do NOT call
each other directly — the OrchestratorAgent handles dispatch.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class BaseAgent(ABC):
    """Abstract base for all report-generation agents.

    Subclasses must define:
    - name: str — human-readable agent name
    - description: str — what this agent does
    - covered_steps: List[int] — which steps (1-12) this agent handles
    """

    name: str = "base"
    description: str = "Base agent"
    covered_steps: List[int] = []

    # Runtime-injected SSE queue (set in run()); class-level default keeps
    # direct think()/act() calls safe when run() hasn't been invoked.
    _stream_queue = None

    def __init__(self, llm_service=None):
        """Initialize the agent.

        Args:
            llm_service: Optional LLM service for AI-powered agents.
                         If None, the agent runs in template-fill mode.
        """
        self._llm = llm_service

    # ---- Public API ----

    async def run(
        self,
        state: dict,
        stream_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """Template method: think → stream thinking → act → validate → update state.

        Args:
            state: AgentState dict (mutated in-place).
            stream_queue: Optional asyncio.Queue for SSE thinking events.

        Returns:
            The updated state dict.
        """
        started_at = time.time()
        self._stream_queue = stream_queue  # Store for sub-agent use

        # 1. Think
        plan = await self.think(state)
        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {
                    "agent": self.name,
                    "status": "thinking",
                    "message": plan.get("summary", f"{self.name} 正在分析..."),
                    "details": plan,
                },
            })

        # 2. Stream thinking steps
        for step in plan.get("steps", []):
            if stream_queue:
                await stream_queue.put({
                    "event": "thinking",
                    "data": {"content": f"🤖 [{self.name}] {step}"},
                })

        # 3. Act
        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {
                    "agent": self.name,
                    "status": "acting",
                    "message": f"{self.name} 正在执行...",
                },
            })

        result = await self.act(state, plan)

        # 4. Validate
        issues = await self.validate(result)
        if issues and stream_queue:
            for issue in issues:
                await stream_queue.put({
                    "event": "thinking",
                    "data": {"content": f"⚠️ [{self.name}] {issue}"},
                })

        # 5. Update state
        state = await self.update_state(state, result)

        elapsed = round(time.time() - started_at, 1)
        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {
                    "agent": self.name,
                    "status": "completed",
                    "message": f"{self.name} 完成（耗时 {elapsed}s）",
                    "issues": issues or [],
                },
            })

        # Log to agent log
        agent_log = state.setdefault("agent_log", [])
        agent_log.append({
            "agent": self.name,
            "timestamp": time.time(),
            "action": plan.get("summary", ""),
            "issues": issues or [],
            "elapsed_sec": elapsed,
        })

        return state

    # ---- Abstract Methods (subclasses MUST override) ----

    @abstractmethod
    async def think(self, state: dict) -> Dict[str, Any]:
        """Analyze current state and produce an execution plan.

        Args:
            state: Current AgentState.

        Returns:
            A plan dict with:
            - summary: str — one-line summary for the UI
            - steps: List[str] — thinking steps to display
            - actions: List[Dict] — specific actions to take
        """
        ...

    @abstractmethod
    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the plan and produce results.

        Args:
            state: Current AgentState.
            plan: The plan dict from think().

        Returns:
            A result dict with agent-specific output fields.
        """
        ...

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Self-check the agent's output. Returns list of issues (empty = clean).

        Override in subclasses for agent-specific validation.
        """
        return []

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Write agent results into the shared state.

        Override in subclasses for agent-specific state updates.
        """
        return state

    async def _emit_thinking(self, content: str) -> None:
        """Emit a thinking/思考 event to show the agent's analysis process.

        These appear as collapsible "思考过程" blocks in the chat UI.
        """
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "thinking",
                "data": {"content": content},
            })

    async def _emit_message(self, content: str, message_type: str = "text") -> None:
        """Emit a chat message via the stream queue so it appears in the chat UI.

        Sub-agents call this to display generated content to the user.

        Args:
            content: Markdown-formatted message content.
            message_type: Message type tag (text, analysis_result, etc.).
        """
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "message",
                "data": {
                    "role": "agent",
                    "content": content,
                    "message_type": message_type,
                },
            })
