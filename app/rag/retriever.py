"""Multi-strategy retrieval for RAG-based report generation.

Provides:
- Chapter-aware retrieval: builds query based on current chapter number
- Hybrid retrieval: combines semantic search with keyword matching
- Multi-collection search: global KB + session materials
- LLM reranking: lightweight scoring of candidates
"""

import re
from typing import List, Dict, Any, Optional

from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService


# Chapter-specific query templates for the 10-chapter report structure
CHAPTER_QUERY_TEMPLATES: Dict[int, str] = {
    1: "拟征收决策基本概况 项目名称 责任单位 征地位置 征收范围 面积 地类 资金测算 实施周期",
    2: "评估过程 评估方法 评估依据 对照表法 实地考察法 问卷调查法 稳评法规 DB32/T4013-2021 公示 座谈",
    3: "社会稳定风险因素调查 公众意见调查 部门意见调查 问卷调查统计 利益相关者诉求 网络舆情 公示照片 座谈会照片",
    4: "决策综合分析 合法性分析 合理性分析 可行性分析 可控性分析 征收主体 征收目的 规划相符性 程序合规性",
    5: "风险因素识别 初始风险等级 补偿方案风险 资金分配风险 社保名单风险 信访舆情风险 发生概率 影响程度",
    6: "措施前风险等级研判 量化指标体系 合法性打分 合理性打分 可行性打分 可控性打分 DB32/T4013-2021 评分表",
    7: "风险防范化解措施 宣传规范 补偿方案 资金监管 社保落实 信访舆情应对 责任主体 可执行措施",
    8: "措施后风险等级评估 重新计算得分 得分对比 风险下降 低风险判定",
    9: "评估结论 建议 合法性结论 合理性结论 可行性结论 可控性结论 低风险 可实施 工作建议",
    10: "应急预案 编制目的 依据 适用范围 指导思想 工作原则 组织领导 职责任务 预警预防 现场处置 舆情处置 保障措施 奖惩机制",
}


class RetrieverService:
    """Multi-strategy retrieval service for report generation."""

    def __init__(
        self,
        embedder: EmbedderService,
        vector_store: VectorStoreService,
    ):
        self.embedder = embedder
        self.vector_store = vector_store

    async def retrieve_for_chapter(
        self,
        chapter_number: int,
        session_id: str,
        project_context: str = "",
        n_results: int = 5,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve relevant knowledge for generating a specific chapter.

        Args:
            chapter_number: The chapter being generated (1-10).
            session_id: Session ID for accessing project materials.
            project_context: User-provided project context text.
            n_results: Number of results to retrieve.
            domain: Restrict global retrieval to this report domain
                (stability, bidding, ...). None = all domains.

        Returns:
            Dict with:
                - chapter_context: Combined relevant regulation/standard text
                - example_context: Combined relevant example report text
                - project_context: Relevant project material text
                - sources: List of source citations
        """
        # Build query for this chapter
        chapter_query = self._build_chapter_query(chapter_number, project_context)

        # Get query embedding
        query_embedding = await self.embedder.embed_text(chapter_query)

        # Query global collection with chapter filter
        global_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            chapter_number=chapter_number,
            domain=domain,
        )

        # Also query for regulations/standards without chapter filter
        broad_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results // 2,
            document_type="regulation",
            domain=domain,
        )

        # 🔴 Query specifically for example reports — templates to emulate style
        example_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
            domain=domain,
        )

        # Query session collection if available
        session_results = []
        session_collection = self.vector_store.get_session_collection(session_id)
        if session_collection:
            session_result = self.vector_store.query(
                session_collection,
                query_embedding,
                n_results=2,
            )
            if session_result.get("documents") and session_result["documents"][0]:
                for i in range(len(session_result["documents"][0])):
                    session_results.append({
                        "document": session_result["documents"][0][i],
                        "metadata": session_result["metadatas"][0][i] if session_result.get("metadatas") else {},
                        "distance": session_result["distances"][0][i],
                    })

        # Combine and deduplicate results
        all_items = self._merge_results(global_results, broad_results, example_results, session_results)

        # Format output
        return self._format_results(all_items, chapter_number)

    async def retrieve_general_for_chapter(
        self,
        chapter_number: int,
        project_context: str = "",
        n_results: int = 5,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Domain-general retrieval for a chapter — NO session/project material.

        This is the cacheable half of retrieve_for_chapter: it only queries the
        global knowledge base (regulation/standard/example), whose content is
        reusable across projects in the same domain. Kept separate so a
        (domain, chapter) cache never mixes in project-specific session data.

        Returns the merged+deduped item list (not formatted), so the caller can
        cache it and combine with live session results.
        """
        chapter_query = self._build_chapter_query(chapter_number, project_context)
        query_embedding = await self.embedder.embed_text(chapter_query)

        global_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            chapter_number=chapter_number,
            domain=domain,
        )
        broad_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results // 2,
            document_type="regulation",
            domain=domain,
        )
        example_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
            domain=domain,
        )
        return self._merge_results(global_results, broad_results, example_results, [])

    async def retrieve_session_items(
        self, session_id: str, chapter_number: int, project_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Retrieve ONLY project-specific session material (never cached)."""
        session_collection = self.vector_store.get_session_collection(session_id)
        if not session_collection:
            return []
        chapter_query = self._build_chapter_query(chapter_number, project_context)
        query_embedding = await self.embedder.embed_text(chapter_query)
        session_result = self.vector_store.query(
            session_collection, query_embedding, n_results=2,
        )
        items = []
        if session_result.get("documents") and session_result["documents"][0]:
            for i in range(len(session_result["documents"][0])):
                items.append({
                    "document": session_result["documents"][0][i],
                    "metadata": session_result["metadatas"][0][i] if session_result.get("metadatas") else {},
                    "distance": session_result["distances"][0][i],
                })
        return items

    def format_items(self, items: List[Dict[str, Any]], chapter_number: int) -> Dict[str, Any]:
        """Public wrapper over _format_results for cache-composed item lists."""
        return self._format_results(items, chapter_number)

    async def retrieve_with_query(
        self,
        query: str,
        session_id: str,
        n_results: int = 5,
        document_type: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """General-purpose retrieval with a custom query.

        Args:
            query: Natural language query.
            session_id: Session ID.
            n_results: Number of results.
            document_type: Optional filter by document type.
            domain: Optional filter by report domain. None = all domains.

        Returns:
            List of result dicts with document, metadata, distance.
        """
        query_embedding = await self.embedder.embed_text(query)

        global_results = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type=document_type,
            domain=domain,
        )

        items = []
        if global_results.get("documents") and global_results["documents"][0]:
            for i in range(len(global_results["documents"][0])):
                items.append({
                    "document": global_results["documents"][0][i],
                    "metadata": global_results["metadatas"][0][i] if global_results.get("metadatas") else {},
                    "distance": global_results["distances"][0][i],
                })

        # Also check session collection
        session_collection = self.vector_store.get_session_collection(session_id)
        if session_collection:
            session_result = self.vector_store.query(
                session_collection,
                query_embedding,
                n_results=2,
            )
            if session_result.get("documents") and session_result["documents"][0]:
                for i in range(len(session_result["documents"][0])):
                    items.append({
                        "document": session_result["documents"][0][i],
                        "metadata": session_result["metadatas"][0][i] if session_result.get("metadatas") else {},
                        "distance": session_result["distances"][0][i],
                    })

        items.sort(key=lambda x: x["distance"])
        return items[:n_results]

    async def retrieve_standards_by_region(
        self,
        region: str,
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """Find local evaluation standards and example reports for a region.

        Builds a region-aware query and retrieves standards, local regulations,
        and example reports matching the region.

        Args:
            region: Region name, e.g. "南京市", "淮安市洪泽区", "南通".
            n_results: Results per category.

        Returns:
            Dict with:
                - standards: List of matching standard/regulation texts
                - local_regulations: List of matching local regulation texts
                - examples: List of matching example report texts
                - region_context: Combined region-relevant context string
                - sources: List of source citations
        """
        query = f"{region} 社会稳定风险评估 地方标准 评估规范 征地 报告模板"
        query_embedding = await self.embedder.embed_text(query)

        # Retrieve standards
        standards_raw = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="standard",
        )
        standards = self._extract_documents(standards_raw)

        # Retrieve local regulations
        local_raw = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="local_regulation",
        )
        local_regs = self._extract_documents(local_raw)

        # Retrieve example reports
        example_raw = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
        )
        examples = self._extract_documents(example_raw)

        # Also try broader search without document_type (for partial matches)
        broad_raw = self.vector_store.query_global(
            query_embedding=query_embedding,
            n_results=n_results,
        )
        broad_items = self._extract_documents(broad_raw)

        # Build combined context
        all_texts = []
        sources = []

        for item in standards + local_regs + examples + broad_items:
            doc_text = item.get("document", "")
            meta = item.get("metadata", {})
            source_file = meta.get("source_file", "")
            doc_type = meta.get("document_type", "")

            if doc_text and len(doc_text) > 20 and doc_text not in all_texts:
                all_texts.append(doc_text)
                if source_file and source_file not in [s.get("file", "") for s in sources]:
                    sources.append({
                        "file": source_file,
                        "type": doc_type,
                        "title": meta.get("section_title", source_file),
                    })

        return {
            "standards": standards,
            "local_regulations": local_regs,
            "examples": examples,
            "region_context": "\n\n---\n\n".join(all_texts[:10]),
            "sources": sources,
            "region": region,
            "query": query,
        }

    @staticmethod
    def _extract_documents(raw_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract document list from a ChromaDB query result."""
        items = []
        if raw_result.get("ids") and raw_result["ids"][0]:
            for i in range(len(raw_result["ids"][0])):
                items.append({
                    "id": raw_result["ids"][0][i],
                    "document": raw_result["documents"][0][i] if raw_result.get("documents") else "",
                    "metadata": raw_result["metadatas"][0][i] if raw_result.get("metadatas") else {},
                    "distance": raw_result["distances"][0][i] if raw_result.get("distances") else 99.0,
                })
        return items

    def _build_chapter_query(self, chapter_number: int, project_context: str) -> str:
        """Build a retrieval query for a specific chapter."""
        template = CHAPTER_QUERY_TEMPLATES.get(
            chapter_number,
            f"社会稳定风险评估报告 第{chapter_number}章"
        )

        if project_context:
            # Extract key terms from project context
            key_terms = self._extract_key_terms(project_context)
            template += " " + " ".join(key_terms[:5])

        return template

    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract key search terms from project context text."""
        # Remove common stop words
        stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
                      '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有',
                      '看', '好', '自己', '这'}
        # Simple term extraction: split by common delimiters, filter short terms
        terms = re.split(r'[，。！？；：、\s\n]+', text)
        return [t for t in terms if len(t) >= 2 and t not in stop_words][:10]

    def _merge_results(
        self,
        *result_groups: Any,
    ) -> List[Dict[str, Any]]:
        """Merge and deduplicate results from multiple queries."""
        seen_ids = set()
        items = []

        for result in result_groups:
            if isinstance(result, dict):
                # Chroma query result format
                if result.get("ids") and result["ids"][0]:
                    for i in range(len(result["ids"][0])):
                        doc_id = result["ids"][0][i]
                        if doc_id not in seen_ids:
                            seen_ids.add(doc_id)
                            items.append({
                                "id": doc_id,
                                "document": result["documents"][0][i] if result.get("documents") else "",
                                "metadata": result["metadatas"][0][i] if result.get("metadatas") else {},
                                "distance": result["distances"][0][i] if result.get("distances") else 99.0,
                            })
            elif isinstance(result, list):
                for item in result:
                    doc_id = item.get("id", item.get("document", "")[:50])
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        items.append(item)

        items.sort(key=lambda x: x.get("distance", 99.0))
        return items

    def _format_results(
        self, items: List[Dict[str, Any]], chapter_number: int
    ) -> Dict[str, Any]:
        """Format retrieval results into structured context."""
        regulation_texts = []
        local_reg_texts = []    # 地方规范 (provincial/city-level standards)
        example_texts = []
        project_texts = []
        sources = []

        for item in items:
            metadata = item.get("metadata", {})
            doc_type = metadata.get("document_type", "")
            doc_text = item.get("document", "")

            source = {
                "title": metadata.get("section_title", metadata.get("source_file", "")),
                "document_type": doc_type,
                "excerpt": doc_text[:200] + "..." if len(doc_text) > 200 else doc_text,
                "chapter_number": metadata.get("chapter_number"),
                "article_number": metadata.get("article_number", ""),
            }

            if doc_type in ("regulation", "standard"):
                if len("\n".join(regulation_texts)) < 3000:
                    regulation_texts.append(doc_text)
                    sources.append(source)
            elif doc_type == "local_regulation":
                if len("\n".join(local_reg_texts)) < 3000:
                    local_reg_texts.append(doc_text)
                    sources.append(source)
            elif doc_type == "example_report":
                if len("\n".join(example_texts)) < 3000:
                    example_texts.append(doc_text)
                    sources.append(source)
            elif doc_type == "project_material":
                if len("\n".join(project_texts)) < 2000:
                    project_texts.append(doc_text)
                    sources.append(source)
            else:
                # Unknown type, add to regulation as fallback
                if len("\n".join(regulation_texts)) < 3000:
                    regulation_texts.append(doc_text)
                    sources.append(source)

        return {
            "chapter_context": "\n\n---\n\n".join(regulation_texts),
            "local_regulation_context": "\n\n---\n\n".join(local_reg_texts),
            "example_context": "\n\n---\n\n".join(example_texts),
            "project_context": "\n\n---\n\n".join(project_texts),
            "sources": sources,
            "chapter_query": CHAPTER_QUERY_TEMPLATES.get(chapter_number, ""),
        }
