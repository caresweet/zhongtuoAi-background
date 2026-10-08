"""字段来源追踪 —— 记录**每个值是谁给的**。

🔴 为什么需要它
────────────────────────────────────────────────────────────────────────
`master_orchestrator.build_project_card` 的 docstring 一直声称
「每个值都标注来源出处（文档/用户提供/AI分析），供后续人工核验」——
**从来没实现过**，是句假注释。而 `_locked_` 一个标记承担了两种含义：

    · 前端弹窗提交 → 意思是「用户填的」
    · 预公告解析   → 意思是「不要被后面的步骤覆盖」
      （见 `routers/report.py` 的 `filled[f"_locked_{_k}"] = True`）

所以 `_locked_` **回答不了**「这个值用户确认过没有」。
而「解析值核对弹窗」要问的恰恰是：**哪些值是机器给的、用户还没看过**。
不先把来源记下来，这个问题就无从判断。

🔴 四类来源

    user      用户在前端弹窗里填 / 确认的
    material  从上传材料里**识别**出来的（预公告、勘测定界、问卷、签到表）
    ai        LLM 提取 / 视觉分析推出来的
    system    系统算出来的（公示期、评分、风险等级）

🔴 存在哪
    存 `filled_data["_field_sources"]`（跟 `_locked_` / `_extracted_tables` 一样
    放在事实卡里）—— 这样它会**跟着 `filled_data` 一起持久化**：
    写回会话（`session_io.put_state`）、归档 `history_reports.db` 都自动带上，
    不用再去 `put_state` 的键列表和 `ReportSession._PERSIST_KEYS` 里各加一处
    （这个仓库已经因为「两处漏加一处」出过多次事故）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable

logger = logging.getLogger(__name__)

USER = "user"
MATERIAL = "material"
AI = "ai"
SYSTEM = "system"

_SOURCES = (USER, MATERIAL, AI, SYSTEM)

# 展示名（弹窗里告诉用户「这个值哪来的」）
SOURCE_LABELS = {
    USER: "用户填写",
    MATERIAL: "材料识别",
    AI: "AI 提取",
    SYSTEM: "系统计算",
}

# 来源优先级：**高的不能被低的覆盖**。
# 🔴 用户填的值绝不能被后来的材料解析/LLM 提取盖掉（业主定过的铁律：
#    `_locked_<key>`（用户填）> 材料识别 > 模板默认）。
_RANK = {USER: 3, MATERIAL: 2, AI: 1, SYSTEM: 0}


def _sources_map(filled: Dict[str, Any]) -> Dict[str, str]:
    """取（必要时创建）来源表。顺带把历史遗留的 `_field_sources` 归一成 dict。"""
    m = filled.get("_field_sources")
    if not isinstance(m, dict):
        m = {}
        filled["_field_sources"] = m
    return m


def mark(state_or_filled: Dict[str, Any], source: str, *keys: str) -> None:
    """记录若干字段的来源。**已记过的不覆盖**（除非新来源优先级更高）。

    Args:
        state_or_filled: `state`（会自动取 `filled_data`）或 `filled_data` 本身。
        source: `USER` / `MATERIAL` / `AI` / `SYSTEM`。
        keys: 字段名。空值 / 下划线开头的键自动跳过。
    """
    if not isinstance(state_or_filled, dict):
        return
    filled = state_or_filled.get("filled_data")
    if not isinstance(filled, dict):
        filled = state_or_filled
    if source not in _SOURCES:
        logger.warning(f"未知字段来源 {source!r}，忽略")
        return
    m = _sources_map(filled)
    for k in keys:
        k = str(k or "").strip()
        if not k or k.startswith("_"):
            continue
        prev = m.get(k)
        if prev and _RANK.get(prev, -1) >= _RANK[source]:
            continue                      # 高优先级来源不被覆盖
        m[k] = source


def mark_written(state_or_filled: Dict[str, Any], source: str,
                 mapping: Dict[str, Any]) -> None:
    """只给**真正写了值**的键记来源（`mapping` 是 {字段: 值}）。"""
    if not isinstance(mapping, dict):
        return
    keys = [k for k, v in mapping.items() if v is not None and str(v).strip()]
    if keys:
        mark(state_or_filled, source, *keys)


def source_of(state_or_filled: Dict[str, Any], key: str) -> str:
    """取字段来源；没记录过返回空串。"""
    if not isinstance(state_or_filled, dict):
        return ""
    filled = state_or_filled.get("filled_data")
    if not isinstance(filled, dict):
        filled = state_or_filled
    m = filled.get("_field_sources")
    return str((m or {}).get(key) or "") if isinstance(m, dict) else ""


def label_of(state_or_filled: Dict[str, Any], key: str) -> str:
    """来源的展示名（「材料识别」/「AI 提取」…）；没记录过返回空串。"""
    s = source_of(state_or_filled, key)
    return SOURCE_LABELS.get(s, "")


# ── 用户确认 ──────────────────────────────────────────────────────
# 🔴 与 `_locked_` **分开**：`_locked_` 还承担着「别覆盖我」的旧含义
#    （材料解析也会打），不能拿它判「用户看过没有」。

def mark_confirmed(state_or_filled: Dict[str, Any], *keys: str) -> None:
    """标记这些字段**用户已经确认过**（弹窗里看过并提交）。"""
    if not isinstance(state_or_filled, dict):
        return
    filled = state_or_filled.get("filled_data")
    if not isinstance(filled, dict):
        filled = state_or_filled
    for k in keys:
        k = str(k or "").strip()
        if k and not k.startswith("_"):
            filled[f"_confirmed_{k}"] = True
