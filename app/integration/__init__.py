"""Integration layer — bridges the zhongtuo-report skills into the FastAPI backend.

Modules:
- product.py: Requirement gathering & field classification (product skill)
- dev.py: Multi-agent generation engine (dev skill)
- test.py: Quality validation & scoring (test skill)
"""

from app.integration.product import ProductIntegration
from app.integration.dev import DevIntegration
from app.integration.test import TestIntegration

__all__ = ["ProductIntegration", "DevIntegration", "TestIntegration"]
