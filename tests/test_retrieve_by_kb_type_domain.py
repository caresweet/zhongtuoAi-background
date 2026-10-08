"""reclamation 域按复垦的类型库检索，绝不落到稳评的 kb_* 库。

锁死两件事：
① 复垦域只取 `kb_reclamation_*`（含「章号未映射」时的兜底），绝不回落稳评库；
② 不传 domain 时稳评取库与查询词与改动前逐字一致（既有调用方行为不变）。

注：本仓未装 pytest-asyncio，异步用例一律用 `asyncio.run`（仓库既有约定，
见 tests/test_llm_tools.py 首行说明），不用 `@pytest.mark.asyncio`。
"""
import asyncio
import inspect

from app.rag import vector_store as vs_mod
from app.rag.retriever import RetrieverService


class _StubEmbedder:
    """最小 embedder：只回一个固定向量，不触网、不落盘。"""

    async def embed_text(self, text: str):
        return [0.0] * 8


def _make_retriever(monkeypatch):
    """构造 RetrieverService，并记录 `get_or_create_collection` 收到的库名。

    collection 名在 `VectorStoreService.get_or_create_collection` 这一步决定
    （不是 retriever 上的方法），所以那里是唯一能观察到库名的桩点；
    原实现把它包在 `try/except` 里，桩点返回 None 会被安全跳过。
    """
    seen = []

    # VectorStoreService.__init__ 会打开真实 Chroma 的 PersistentClient，
    # 本用例只关心库名，故令其构造为空操作 —— 不创建任何真实库文件。
    monkeypatch.setattr(vs_mod.VectorStoreService, "__init__",
                        lambda self, *a, **k: None)

    def _fake_get_or_create(self, name, description=""):
        seen.append(name)
        return None

    monkeypatch.setattr(vs_mod.VectorStoreService, "get_or_create_collection",
                        _fake_get_or_create)

    retriever = RetrieverService(
        embedder=_StubEmbedder(),
        vector_store=vs_mod.VectorStoreService(),
    )
    return retriever, seen


def test_signature_accepts_domain():
    sig = inspect.signature(RetrieverService.retrieve_by_kb_type)
    assert "domain" in sig.parameters


def test_reclamation_unmapped_chapter_uses_reclamation_default(monkeypatch):
    """未映射的章号（如 11）兜到复垦主入口库，绝不落到稳评 kb_decision_info。"""
    retriever, seen = _make_retriever(monkeypatch)
    asyncio.run(retriever.retrieve_by_kb_type(11, "测试", domain="reclamation"))
    assert seen == ["kb_reclamation_preface"], seen


def test_reclamation_uses_exact_reclamation_collections(monkeypatch):
    """第 1 章主库 kb_reclamation_preface、备选 kb_reclamation_overview —— 断言**精确库名**。

    若实现误加前缀（kb_kb_reclamation_preface）或错取稳评库，这条立即失败；
    只断言「以 kb_reclamation_ 开头」的话，双前缀 `kb_kb_reclamation_preface`
    也能蒙混过关，故必须比对精确值。
    """
    retriever, seen = _make_retriever(monkeypatch)
    asyncio.run(retriever.retrieve_by_kb_type(1, "测试", domain="reclamation"))
    assert seen == ["kb_reclamation_preface", "kb_reclamation_overview"], seen


def test_stability_path_unchanged_without_domain(monkeypatch):
    """不传 domain：即使复垦已接入，稳评仍取原 kb_* 库（回归锁，防改坏既有行为）。"""
    retriever, seen = _make_retriever(monkeypatch)
    asyncio.run(retriever.retrieve_by_kb_type(11, "测试"))
    assert seen == ["kb_risk_source", "kb_stakeholder_ref"], seen
