"""地图类图片有专用提示词，且要求输出可解析的位置信息。"""
from app.services.image_analyzer import ANALYSIS_PROMPTS


def test_map_prompt_exists():
    assert "map" in ANALYSIS_PROMPTS


def test_map_prompt_asks_for_structured_location():
    p = ANALYSIS_PROMPTS["map"]
    for kw in ("地名", "四至", "方位", "JSON"):
        assert kw in p, kw


def test_existing_prompts_untouched():
    """四个既有提示词的首句必须**内容不变**。

    ⚠️ **只断言「键还在」是不够的** —— `assert k in ANALYSIS_PROMPTS` 在改掉
    `notice` 里一个词之后**依然是绿的**（实测：把「征收目的」改成「征收宗旨」，
    该断言照样通过）。那样这条测试就不在守它声称要守的东西。

    ⚠️ **本断言的边界（实测）**：只钉住**首句**。
    - 改首句内的词 → 变红 ✅
    - 改首句**之外**的正文 → **仍绿** ⚠️

    **为什么不升级成全等断言**：这四条是**稳评的**提示词、会随稳评业务演进，
    而本文件是复垦的测试。把稳评整段提示词冻死在这里，等于**每次稳评改写都要来改复垦的测试** ——
    错误的耦合。本任务「有没有误改」的真正保障是**审查者读 diff**；
    这条测试定位为**低耦合的回归护栏**，拦截「编辑这个字典时手滑碰到邻项」这类事故。
    """
    assert "请分析这张征地公告/预公告图片，提取以下结构化信息：" in ANALYSIS_PROMPTS["notice"]
    assert "请分析这张问卷调查表图片（逐张勾选式问卷 或 汇总统计表），提取信息。" in ANALYSIS_PROMPTS["survey"]
    assert "请描述这张照片的内容，用于社会稳定风险评估报告配图。包括：" in ANALYSIS_PROMPTS["photo"]
    assert "请分析这张图片，提取与社会稳定风险评估报告编制相关的所有信息。" in ANALYSIS_PROMPTS["general"]
