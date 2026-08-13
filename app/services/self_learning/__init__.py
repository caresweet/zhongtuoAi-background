"""自主学习进化系统 — 识别、爬取、清洗、拆解、入库、生成、回写。

核心组件：
- cleaner: 严格版清洗（复用 cleaning_pipeline 的三个严格 handler）
- tags: 隔离标签（类型/地区/租户/年份/是否现行，防混用）
- type_recognizer: 类型识别 + 去重
- web_crawler: 联网爬取合规优秀报告
- report_decomposer: LLM 驱动拆解报告 schema
"""
