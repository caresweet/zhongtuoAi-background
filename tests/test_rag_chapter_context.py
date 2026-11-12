# -*- coding: utf-8 -*-
"""RAG：类型库范文之外，必须再补一层法规/标准（chapter_context 不能恒为空）。"""
import pytest


class _FakeRetriever:
    def __init__(self):
        self.items = None
        self.general_called = 0

    async def retrieve_by_kb_type(self, chapter_number, project_context="",
                                  main_results=8, alt_results=3, domain=None):
        return [{"document": "范文正文", "metadata": {"kb_type": "risk_source"},
                 "distance": 0.1}]

    async def retrieve_general_for_chapter(self, chapter_number, project_context="",
                                           n_results=5, domain=None):
        self.general_called += 1
        return [
            {"document": "第二十九条 社会稳定风险评估……", "metadata":
             {"document_type": "regulation"}, "distance": 0.2},
            {"document": "范文二", "metadata": {"document_type": "example_report"},
             "distance": 0.3},
        ]

    async def retrieve_session_items(self, session_id, chapter_number,
                                     project_context=""):
        return []

    def _merge_results(self, *lists):
        return [x for lst in lists for x in (lst or [])]

    def format_items(self, items, chapter_number):
        self.items = items
        return {"chapter_context": "ok", "sources": items}


class _FakeCache:
    @staticmethod
    def make_key(domain, chapter, query):
        return f"{domain}:{chapter}:{query}"

    def get(self, key):
        return None

    def put(self, *a, **k):
        return None


def test_regulations_are_merged_with_examples(monkeypatch):
    from app.rag.rag_service import rag_service

    fake = _FakeRetriever()
    monkeypatch.setattr(rag_service, "_retriever", fake)
    monkeypatch.setattr(rag_service, "_cache", _FakeCache())

    import asyncio
    asyncio.run(rag_service.retrieve_for_chapter(
        11, "s1", "测试项目 土地征收", n_results=6, domain="stability"))

    assert fake.general_called == 1
    types = {str((it.get("metadata") or {}).get("document_type") or "")
             for it in fake.items}
    docs = {it.get("document") for it in fake.items}
    assert "regulation" in types          # 法规进来了
    assert "范文正文" in docs             # 类型库范文也还在
