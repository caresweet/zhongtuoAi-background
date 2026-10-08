"""Domain registry — single lookup + detection point for report domains.

Replaces the scattered keyword branching (_BIDDING_KEYWORDS in master.py,
intent matching in report.py) with one place that:
  - holds all registered DomainConfig objects,
  - resolves a domain by id (get_domain),
  - detects a domain from a user message / explicit intent (detect_domain).

Adding a new report type = add a build_*_config() and register it here.
"""

import logging
from typing import Dict, List, Optional

from app.domains.base import DomainConfig

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN = "stability"

_REGISTRY: Dict[str, DomainConfig] = {}

# 注册表为空时的降级配置 —— 只在**所有 builder 都建造失败**时才会用到。
# 目的是让 get_domain 兑现自己 docstring 里的承诺（回落默认域），
# 而不是把 _REGISTRY[...] 的 KeyError 泄漏给调用方，
# 那会把一次静默降级变成稳评主链路上的 500。
_FALLBACK_CONFIG = DomainConfig(
    domain_id=DEFAULT_DOMAIN,
    display_name="社会稳定风险评估报告",
    identity_prompt="",
    rag_domain=DEFAULT_DOMAIN,
    chapter_structure={},
    default_collection="knowledge_base",
)


def _ensure_loaded():
    """Lazily build and register configs (avoids import cycles at module load)."""
    if _REGISTRY:
        return
    from app.domains.stability import build_stability_config
    from app.domains.bidding import build_bidding_config
    from app.domains.reclamation import build_reclamation_config

    for builder in (build_stability_config, build_bidding_config,
                    build_reclamation_config):
        try:
            cfg = builder()
            _REGISTRY[cfg.domain_id] = cfg
        except Exception as e:
            logger.error("Failed to build domain config from %s: %s", builder, e)


def get_domain(domain_id: Optional[str]) -> DomainConfig:
    """Return a DomainConfig by id, falling back to the default domain."""
    _ensure_loaded()
    if domain_id and domain_id in _REGISTRY:
        return _REGISTRY[domain_id]
    cfg = _REGISTRY.get(DEFAULT_DOMAIN)
    if cfg is None:
        # 注册表为空（所有 builder 都失败）—— 记 error 让它可见，但不抛。
        logger.error("域注册表为空，回落最小降级配置；请检查各域 builder 的报错日志")
        return _FALLBACK_CONFIG
    return cfg


def domain_of_state(state: Optional[dict]) -> str:
    """从会话 state 里取出已钉住的域，缺省回落稳评。

    `_domain` / `_conversation_domain` 由 report.py 的意图识别写入会话 state。
    🔴 这是全仓唯一的取域表达式：调用点（章节工厂、附件解析、报告装配）
       都从这里取，避免 fallback 链散落多处后各自漂移。
    """
    state = state or {}
    return state.get("_domain") or state.get("_conversation_domain") or DEFAULT_DOMAIN


def list_domains() -> List[DomainConfig]:
    _ensure_loaded()
    return list(_REGISTRY.values())


def detect_domain(message: str = "", explicit: Optional[str] = None,
                  state: Optional[dict] = None) -> str:
    """Resolve the domain id for a request.

    Priority:
      1. explicit intent from the frontend (if it names a known domain),
      2. a domain already pinned on the conversation state,
      3. keyword match against each domain's classify_keywords,
      4. DEFAULT_DOMAIN.
    """
    _ensure_loaded()

    if explicit and explicit in _REGISTRY:
        return explicit

    if state:
        pinned = state.get("_domain") or state.get("_conversation_domain")
        if pinned in _REGISTRY:
            return pinned

    if message:
        # Non-default domains win over the default when their keywords match,
        # so a bidding message isn't swallowed by the default domain.
        for cfg in _REGISTRY.values():
            if cfg.domain_id == DEFAULT_DOMAIN:
                continue
            if cfg.matches(message):
                return cfg.domain_id
        # Then check the default domain's own keywords.
        default_cfg = _REGISTRY.get(DEFAULT_DOMAIN)
        if default_cfg and default_cfg.matches(message):
            return DEFAULT_DOMAIN

    return DEFAULT_DOMAIN
