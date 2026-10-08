"""复垦的库名与稳评物理分离；缺省域仍回落稳评库（既有行为，锁死防改坏）。"""
from app.rag.vector_store import VectorStoreService


def test_reclamation_never_maps_to_stability_collection():
    """复垦落在自己的库，绝不等同稳评/投标的库。

    🔴 复垦的**主入口库**是 `kb_reclamation_preface`（灌库脚本实际建的 12 个
       `kb_reclamation_*` 类型库之一），与 `domains/reclamation.default_collection`
       以及 retriever 的未映射章兜底一致 —— 不是从未被创建的 `kb_reclamation`。
    """
    name = VectorStoreService.get_domain_collection_name("reclamation")
    assert name == "kb_reclamation_preface"
    assert name.startswith("kb_reclamation_")
    assert name != "knowledge_base"
    assert name != "bidding_knowledge"


def test_reclamation_type_collections_are_prefixed():
    """复垦的分章类型库一律以 kb_reclamation_ 开头，且引用到的键都真实存在。

    注意断言的是 COLLECTIONS 的**值**（库名），不是 RECLAMATION_KB_TYPES 的值
    —— 后者是语义类名（"前言"…），不是 collection 名。
    """
    from app.rag.reclamation_kb_map import COLLECTIONS, RECLAMATION_KB_TYPES

    for coll in set(COLLECTIONS.values()):
        assert coll.startswith("kb_reclamation_")

    # 🔴 章 → 类型库 的每个**键**（主类型库 + 每个备选库）都必须在 COLLECTIONS 里：
    #    写错一个字，`collection_for` 会在第 N 章检索时才抛出，或更糟 ——
    #    被 `try/except` 吞掉、该章静默少范文。这条断言让它在测试时就失败。
    for chapter, (main, alts) in RECLAMATION_KB_TYPES.items():
        assert main in COLLECTIONS, f"第{chapter}章 主类型库 {main!r} 不在 COLLECTIONS"
        for alt in alts:
            assert alt in COLLECTIONS, f"第{chapter}章 备选库 {alt!r} 不在 COLLECTIONS"


def test_none_still_falls_back_to_stability():
    """该分支只在调用方**真的没给** domain 时触发（属漏传，故代码里用 warning 让它可见）。

    稳评的正常路径传的是**显式的** "stability"，经 DOMAIN_COLLECTIONS 落到
    knowledge_base —— 不会走到这个 None 回落分支。这条测试锁死后者行为不变。
    """
    assert VectorStoreService.get_domain_collection_name(None) == "knowledge_base"
