"""地图类图片有专用提示词，且要求输出可解析的位置信息。"""
from app.services.image_analyzer import ANALYSIS_PROMPTS


def test_map_prompt_exists():
    assert "map" in ANALYSIS_PROMPTS


def test_map_prompt_asks_for_structured_location():
    p = ANALYSIS_PROMPTS["map"]
    for kw in ("地名", "四至", "方位", "JSON"):
        assert kw in p, kw


def test_existing_prompts_untouched():
    for k in ("notice", "survey", "photo", "general"):
        assert k in ANALYSIS_PROMPTS
