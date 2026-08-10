"""DomainConfig — a report domain as a first-class, config-driven object.

A "domain" is one kind of report the system can produce (社会稳定风险评估,
招标投标文件, 可行性研究报告, ...). Historically the system hard-coded two
domains with branching `if bidding: ... else: ...` logic scattered across
master.py, report.py, and the RAG layer. This module collapses that into a
single declarative object so that adding a new report type means registering a
new DomainConfig — not editing core code.

A DomainConfig bundles everything domain-specific:
- identity_prompt:  the persona/system prompt the master agent adopts
- rag_domain:       the `domain` metadata tag used to scope knowledge retrieval
- classify_keywords: regex/keywords that detect this domain from a user message
- chapter_structure: the report's section structure (data, not code)
- pipeline:         which generation pipeline to run ("chapter_by_chapter" | "single_shot")
- data_categories:  the fields users must supply

Structures are referenced, not duplicated: stability points at the existing
CHAPTER_DEFINITIONS; bidding points at the existing bidding report types.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern


@dataclass
class DomainConfig:
    """Declarative configuration for one report domain."""

    domain_id: str                       # stable id: "stability", "bidding", ...
    display_name: str                    # human label: "社会稳定风险评估"
    identity_prompt: str                 # master-agent system prompt for this domain
    rag_domain: str                      # `domain` metadata tag for RAG scoping
    pipeline: str = "chapter_by_chapter"  # generation strategy
    classify_keywords: List[str] = field(default_factory=list)
    chapter_structure: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    data_categories: Dict[str, Any] = field(default_factory=dict)
    default_collection: str = "knowledge_base"
    company_name: str = ""
    # Optional: forbidden references / domain guardrails (e.g. bidding must not
    # cite DB32/T4013 stability standards).
    guardrails: List[str] = field(default_factory=list)

    _compiled: Optional[Pattern] = field(default=None, repr=False)

    @property
    def keyword_pattern(self) -> Optional[Pattern]:
        if self.classify_keywords and self._compiled is None:
            self._compiled = re.compile(
                "(?:" + "|".join(re.escape(k) for k in self.classify_keywords) + ")",
                re.IGNORECASE,
            )
        return self._compiled

    def matches(self, message: str) -> bool:
        """True if a user message looks like it belongs to this domain."""
        pat = self.keyword_pattern
        return bool(pat and message and pat.search(message))

    @property
    def chapter_count(self) -> int:
        return len(self.chapter_structure)
