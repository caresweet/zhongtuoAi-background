"""建会话时的「域装束」—— 章集合 / 兜底标题 / 模板名一律按域取。

本分支要治的最后一处：`state.create_initial_state` 无条件铺稳评的 14 章与
「社会稳定风险评估报告」抬头，复垦会话一建出来就顶着稳评的章节列表
（`GET /generate/{sid}/chapters` 直接可见）。修法：域差异收口在
`chapter_catalog`，`create_initial_state` 按域取，自己不分域。
"""
from app.agent import chapter_catalog as cat
from app.agent.state import create_initial_state


def test_stability_default_state_is_untouched():
    """稳评（含不传域的默认）会话 state 逐字段等于改动前 —— 稳评护栏。

    注意 `template_name` 断言的是**会话最终可见值**（旧路径里由 report.py 写死的
    「…（知识库综合生成）」）。create_initial_state 现在直接产出同一个串，
    故建出来的会话 state 逐字不变。
    """
    st = create_initial_state("sid-s")
    assert sorted(st["chapters"]) == list(range(1, 15))
    assert st["chapters"][3]["title"] == "江苏省淮安市社会稳定风险评估评审表"
    assert st["report_title"] == "社会稳定风险评估报告"
    assert st["template_name"] == "内置十四章案卷标准结构（知识库综合生成）"
    # 不传域 == 显式传稳评
    assert create_initial_state("sid-s", domain="stability") == st


def test_reclamation_state_gets_reclamation_chapters_and_title():
    st = create_initial_state("sid-r", domain="reclamation")
    assert sorted(st["chapters"]) == list(range(1, 11))
    assert st["chapters"][1]["title"] == "前言"
    assert st["chapters"][3]["title"] == "项目概况"       # 不是稳评的评审表
    assert st["chapters"][10]["title"] == "保障措施"
    assert st["report_title"] == "土地复垦方案"
    # 🔴 复垦不得顶着稳评的「十四章」字样
    assert st["template_name"] == "内置复垦方案标准结构（知识库综合生成）"
    assert "十四" not in st["template_name"]


def test_catalog_template_name_by_domain():
    assert cat.template_name("stability") == "内置十四章案卷标准结构（知识库综合生成）"
    assert cat.template_name(None) == cat.template_name("stability")   # 默认域 = 稳评
    assert cat.template_name("reclamation") == "内置复垦方案标准结构（知识库综合生成）"


def test_state_does_not_seed_stability_chapters_directly():
    """`create_initial_state` 不得再直接铺稳评的 CHAPTER_DEFINITIONS。

    源码级断言（与 test_reclamation_domain_propagation 同法）：漏改一次就红。
    """
    import inspect
    from app.agent import state as state_mod
    src = inspect.getsource(state_mod.create_initial_state)
    # 章目录必须经 chapter_catalog 按域取，而不是读本模块的稳评 CHAPTER_DEFINITIONS
    assert "chapter_titles(" in src
    assert "CHAPTER_DEFINITIONS" not in src
    # 兜底标题 / 模板名也按域取
    assert "report_kind_name(" in src
    assert "template_name(" in src
