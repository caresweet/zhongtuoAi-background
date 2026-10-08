"""Integration layer — bridges the zhongtuo-report skills into the FastAPI backend.

Modules:
- product.py: Requirement gathering & field classification (product skill)
- test.py: Quality validation & scoring (test skill)
- template_parser.py: 解析 .docx 模板占位符（product.py 使用）

注：原 `dev.py`（多 Agent 生成引擎）、`multi_agent.py`、`report_filler.py`、
`test_runner.py` 四个模块**无任何调用方**（旧十章结构时代的脚手架），已删除；
其章节相关逻辑早已被 `app/services/report_workflow.py` + `ChapterComposer` 取代。
"""

from app.integration.product import ProductIntegration
from app.integration.test import TestIntegration

__all__ = ["ProductIntegration", "TestIntegration"]
