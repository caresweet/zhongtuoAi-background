"""类型识别 + 去重 — 判断用户报告是已知类型还是新类型。

三级判断：
1. 关键词匹配（快）：复用 domain_registry 的 classify_keywords
2. LLM 语义相似度（准）：识别"换说法"的已知类型（如"社会稳定性评估" = "社会稳定风险评估"）
3. 真正新类型：需要联网学习

去重铁律：库里已有类型、或与已知类型语义高度相似（只是换说法）的，一律跳过学习。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RecognitionResult:
    """类型识别结果。"""
    domain_id: Optional[str]        # 已知类型 id，None = 新类型
    is_known: bool                  # 是否已知类型（已知/换说法 = True，真正新类型 = False）
    source: str                     # keyword / similarity / new
    similarity_score: float = 0.0   # 语义相似度（0-1）
    matched_domain_name: str = ""   # 匹配到的已知类型名称


class TypeRecognizer:
    """识别报告类型，判断是否需要联网学习。"""

    async def recognize(
        self,
        message: str,
        materials_text: str = "",
        llm_service=None,
    ) -> RecognitionResult:
        """识别用户报告类型。

        Args:
            message: 用户消息（如"帮我写一份可行性研究报告"）
            materials_text: 上传资料文本（辅助判断）
            llm_service: LLM 服务（用于语义相似度判断）

        Returns:
            RecognitionResult — is_known=True 则跳过学习，直接用已知类型生成；
            is_known=False 则是新类型，需联网学习。
        """
        combined = f"{message}\n{materials_text[:500]}" if materials_text else message

        # ── 1. 关键词匹配（快，免费）──
        from app.domains import registry
        registry._ensure_loaded()  # 确保已加载
        from app.domains.registry import _REGISTRY
        for domain_id, cfg in _REGISTRY.items():
            if cfg.matches(combined):
                return RecognitionResult(
                    domain_id=domain_id, is_known=True, source="keyword",
                    matched_domain_name=cfg.display_name,
                )

        # ── 2. LLM 语义相似度（准，识别"换说法"）──
        if llm_service:
            similar = await self._llm_similarity(combined, _REGISTRY, llm_service)
            if similar:
                return similar

        # ── 3. 真正新类型 ──
        return RecognitionResult(
            domain_id=None, is_known=False, source="new",
        )

    async def _llm_similarity(
        self, message: str, registry: dict, llm_service
    ) -> Optional[RecognitionResult]:
        """用 LLM 判断 message 是否等价于某个已知类型（换说法）。"""
        domain_descriptions = "\n".join(
            f"- {cid}: {cfg.display_name}（关键词：{'、'.join(cfg.classify_keywords[:5])}）"
            for cid, cfg in registry.items()
        )

        prompt = f"""你是报告类型识别助手。判断用户想要的报告类型是否等价于下列已知类型之一。

## 已知报告类型
{domain_descriptions}

## 用户需求
{message[:500]}

## 判断规则
- 如果用户需求与某个已知类型**语义等价**（只是换了说法，如"社会稳定性评估"="社会稳定风险评估"），返回该类型 id，similarity 0.85-1.0
- 如果用户需求是**全新的报告类型**（如可行性研究、环评），返回 similarity < 0.5，matched 为 null
- 如果相似但不足以归为已知（0.5-0.85），返回 matched=null

## 输出 JSON 格式
{{"matched": "stability" 或 null, "similarity": 0.0-1.0, "reason": "一句话说明"}}
"""

        try:
            result = await llm_service.chat_with_reasoning(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256, temperature=0.1,
            )
            content = result.get("content", "")
            json_match = json.loads(_extract_json(content))
            matched = json_match.get("matched")
            similarity = float(json_match.get("similarity", 0))

            if matched and matched in registry and similarity >= 0.85:
                return RecognitionResult(
                    domain_id=matched, is_known=True, source="similarity",
                    similarity_score=similarity,
                    matched_domain_name=registry[matched].display_name,
                )
            return None
        except Exception as e:
            logger.warning(f"LLM similarity detection failed: {e}")
            return None


def _extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 部分。"""
    import re
    m = re.search(r'\{[\s\S]*\}', text)
    return m.group(0) if m else "{}"


# Singleton
type_recognizer = TypeRecognizer()
