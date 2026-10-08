# -*- coding: utf-8 -*-
"""「数据库模板 → 切块 → 按正文章号映射喂给章节 agent」的回归测试。

模板本身是**案卷式**（12 块），本系统输出是 **DB32/T4013-2021 十章**，
两者结构不同，经 `DB32_TO_TEMPLATE_KEY` 映射取块。

运行：cd backend && python -m pytest tests/test_template_chunker.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.chapter_definitions import TOTAL_CHAPTERS, ALL_CHAPTERS, get_chapter_title
from app.services.template_chapter_chunker import (
    build_and_cache, get_chapter_template_chunk, build_template_block_for_prompt,
)
from app.agent.agents.chapters import get_chapter_agent


def test_template_splits_into_blocks():
    """模板必须能按「第X章」切成块（评审表已剔除）。"""
    data = build_and_cache()
    if not data.get("chapters"):
        import pytest
        pytest.skip("未找到可用的稳评报告模板（数据库 templates 表 / 缓存）")
    # 模板是案卷式（摘掉评审表后 11 块），与输出结构的 14 章数量不同，靠映射取块
    assert len(data["chapters"]) >= 10


def test_each_db32_chapter_maps_to_a_template_block():
    """每个 DB32 正文章号都应能通过映射取到模板底稿（内容非空）。"""
    data = build_and_cache()
    if not data.get("chapters"):
        return
    from app.services.template_chapter_chunker import DB32_TO_TEMPLATE_KEY
    for n in ALL_CHAPTERS:
        key = DB32_TO_TEMPLATE_KEY.get(n)
        assert key is not None, f"第{n}章缺少模板映射"
        if not key:
            continue          # 第3章=评审表，用专门的评审表模板，不取正文底稿
        if n == 14:
            continue          # 附件章由组装器按附件清单挂载文件，不取正文底稿
        ch = data["chapters"].get(key) or {}
        assert (ch.get("text") or "").strip(), \
            f"第{n}章（{get_chapter_title(n)}）映射到模板块 {key} 但内容为空"


def test_each_chapter_agent_has_its_own_chunk():
    """每个正文章节 agent 都应有可用的模板底稿（映射后允许若干章共享同一块）。"""
    from app.services.template_chapter_chunker import get_chapter_template_chunk
    data = build_and_cache()
    if not data.get("chapters"):
        return
    for n in ALL_CHAPTERS:
        if n in (3, 14):
            continue          # 评审表章走专门模板；附件章由组装器挂载文件
        assert (get_chapter_template_chunk(n) or "").strip(), \
            f"第{n}章（{get_chapter_title(n)}）没有模板底稿"

def test_agents_registered():
    """工厂必须能为 14 个正文章各返回一个 agent。"""
    from app.agent.agents.chapters import get_review_table_agent
    for n in ALL_CHAPTERS:
        agent = get_chapter_agent(n)
        assert agent is not None, f"第{n}章未返回 agent"
        assert hasattr(agent, "run"), f"第{n}章 agent 缺少 run(state, queue) 契约"
    assert get_chapter_agent(0) is None
    assert get_chapter_agent(99) is None
    assert "评审表" in get_review_table_agent().chapter_title
    assert hasattr(get_review_table_agent(), "render_into")
    # 🔴 章节标题以 chapter_definitions（唯一事实源）为准，不写死
    assert get_chapter_agent(1).chapter_title == get_chapter_title(1)
    assert get_chapter_agent(1).chapter_title == "编制说明"
