"""Report domain registry — config-driven multi-domain support.

A domain = one kind of report (stability / bidding / ...). See base.DomainConfig.
"""

from app.domains.base import DomainConfig
from app.domains.registry import (
    get_domain,
    list_domains,
    detect_domain,
    DEFAULT_DOMAIN,
)

__all__ = [
    "DomainConfig",
    "get_domain",
    "list_domains",
    "detect_domain",
    "DEFAULT_DOMAIN",
]
