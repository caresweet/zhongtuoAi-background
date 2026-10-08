"""复垦的库名与稳评物理分离；缺省域仍回落稳评库（既有行为，锁死防改坏）。"""
from app.rag.vector_store import VectorStoreService


def test_reclamation_never_maps_to_stability_collection():
    name = VectorStoreService.get_domain_collection_name("reclamation")
    assert name == "kb_reclamation"
    assert name != "knowledge_base"
    assert name != "bidding_knowledge"


def test_reclamation_type_collections_are_prefixed():
    """复垦的分章类型库一律以 kb_reclamation_ 开头。

    注意断言的是 COLLECTIONS 的**值**（库名），不是 RECLAMATION_KB_TYPES 的值
    —— 后者是语义类名（"前言"…），不是 collection 名。
    """
    from app.rag.reclamation_kb_map import COLLECTIONS

    for coll in set(COLLECTIONS.values()):
        assert coll.startswith("kb_reclamation_")


def test_none_still_falls_back_to_stability():
    """稳评路径有大量调用不传 domain 并依赖 knowledge_base —— 这条行为不许变。"""
    assert VectorStoreService.get_domain_collection_name(None) == "knowledge_base"
