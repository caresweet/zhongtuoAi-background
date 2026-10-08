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


# Chapter-specific query templates —— 单一事实源：chapter_definitions.CHAPTER_RAG_QUERIES
from app.agent.chapter_definitions import CHAPTER_RAG_QUERIES as CHAPTER_QUERY_TEMPLATES
from app.agent.chapter_definitions import ALL_CHAPTERS  # noqa: F401


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

        # 🔴 物理隔离：按 domain 查对应 collection，不跨类型
        collection_name = self.vector_store.get_domain_collection_name(domain)

        # Query domain collection with chapter filter
        global_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            chapter_number=chapter_number,
        )

        # Also query for regulations/standards without chapter filter
        broad_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results // 2,
            document_type="regulation",
        )

        # 🔴 Query specifically for example reports — templates to emulate style
        example_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
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

        # 🔴 物理隔离：按 domain 查对应 collection，不跨类型，不用 domain 标签软过滤
        collection_name = self.vector_store.get_domain_collection_name(domain)

        global_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            chapter_number=chapter_number,
        )
        broad_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results // 2,
            document_type="regulation",
        )
        example_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
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

        # 🔴 物理隔离：按 domain 查对应 collection，不跨类型
        collection_name = self.vector_store.get_domain_collection_name(domain)
        global_results = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type=document_type,
        )

        items = []
        if global_results.get("documents") and global_results["documents"][0]:
            for i in range(len(global_results["documents"][0])):
                meta = global_results["metadatas"][0][i] if global_results.get("metadatas") else {}
                if not isinstance(meta, dict):
                    meta = {}
                items.append({
                    "id": str(meta.get("chunk_id", "")) or f"g_{i}",
                    "text": global_results["documents"][0][i],
                    "document": global_results["documents"][0][i],
                    "metadata": meta,
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
                    meta = session_result["metadatas"][0][i] if session_result.get("metadatas") else {}
                    if not isinstance(meta, dict):
                        meta = {}
                    items.append({
                        "id": str(meta.get("chunk_id", "")) or f"s_{i}",
                        "text": session_result["documents"][0][i],
                        "document": session_result["documents"][0][i],
                        "metadata": meta,
                        "distance": session_result["distances"][0][i],
                    })

        items.sort(key=lambda x: x["distance"])
        return items[:n_results]

    async def retrieve_standards_by_region(
        self,
        region: str,
        n_results: int = 5,
        domain: Optional[str] = "stability",
    ) -> Dict[str, Any]:
        """Find local evaluation standards and example reports for a region.

        Builds a region-aware query and retrieves standards, local regulations,
        and example reports matching the region.

        Args:
            region: Region name, e.g. "南京市", "淮安市洪泽区", "南通".
            n_results: Results per category.
            domain: report domain（物理隔离：只在对应 collection 检索）。

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

        # 🔴 物理隔离：按 domain 查对应 collection
        collection_name = self.vector_store.get_domain_collection_name(domain)

        # Retrieve standards
        standards_raw = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="standard",
        )
        standards = self._extract_documents(standards_raw)

        # Retrieve local regulations
        local_raw = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="local_regulation",
        )
        local_regs = self._extract_documents(local_raw)

        # Retrieve example reports
        example_raw = self.vector_store.query_by_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            n_results=n_results,
            document_type="example_report",
        )
        examples = self._extract_documents(example_raw)

        # Also try broader search without document_type (for partial matches)
        broad_raw = self.vector_store.query_by_collection(
            collection_name=collection_name,
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

    # ═══════════════════════════════════════════════════════════════
    # 内容类型库检索（kb_* 集合，按章节 → 内容类型映射）
    # ═══════════════════════════════════════════════════════════════
    async def retrieve_by_kb_type(
        self,
        chapter_number: int,
        project_context: str = "",
        main_results: int = 8,
        alt_results: int = 3,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按「章 → 内容类型库」取范文正文。

        domain=None 时沿用稳评映射（既有调用方行为不变）；
        domain="reclamation" 时走复垦映射，库名一律 kb_reclamation_*。
        返回裸 item 列表（未经 format），供 rag_service 合并/格式化。
        """
        # 🔴 两域的映射表**形状不同**，务必各自归一成「最终 collection 名」：
        #    稳评 CHAPTER_KB_TYPES 的值是语义类名（risk_source…），库名要加 `kb_` 前缀；
        #    复垦 RECLAMATION_KB_TYPES 的值是 reclamation_kb_map 的语义键，经 COLLECTIONS
        #    取出后**已是完整库名**（kb_reclamation_preface），绝不能再套 `f"kb_{...}"`
        #    —— 那样会得到幽灵库 kb_kb_reclamation_preface，它在 `try/except` 里静默取不到
        #    任何东西，报告就悄悄少了范文。库名的唯一事实源是 reclamation_kb_map.COLLECTIONS。
        #    desc_map 单独保存「查询词描述」，使稳评查询串与改动前逐字一致。
        if domain == "reclamation":
            from app.rag.reclamation_kb_map import RECLAMATION_KB_TYPES, collection_for
            kb_map = {n: (collection_for(m), [collection_for(a) for a in alt])
                      for n, (m, alt) in RECLAMATION_KB_TYPES.items()}
            desc_map = {n: m for n, (m, _) in RECLAMATION_KB_TYPES.items()}
            # 章号未映射时兜到复垦主入口库（对齐 domains/reclamation 的
            # default_collection="kb_reclamation_preface"），绝不回落到稳评库
            default_colls, default_desc = (collection_for("前言"), []), "前言"
        else:
            from app.rag.kb_chapter_map import CHAPTER_KB_TYPES
            from app.rag.kb_types_config import TYPES
            kb_map = {n: (f"kb_{m}", [f"kb_{a}" for a in alt])
                      for n, (m, alt) in CHAPTER_KB_TYPES.items()}
            desc_map = {n: TYPES.get(m, m) for n, (m, _) in CHAPTER_KB_TYPES.items()}
            default_colls = ("kb_decision_info", [])
            default_desc = TYPES.get("decision_info", "decision_info")

        main_coll, alt_colls = kb_map.get(chapter_number, default_colls)

        # 构建语义化查询词：类型说明 + 项目关键词
        main_desc = desc_map.get(chapter_number, default_desc)
        query = f"{main_desc} {project_context[:80]}"
        query_embedding = await self.embedder.embed_text(query)

        # 🔴 复用自身 vector_store（避免新建 ChromaDB 连接与主进程锁冲突）
        vs = self.vector_store
        collections = []
        try:
            c = vs.get_or_create_collection(main_coll)
            if c.count():
                collections.append(c)
        except Exception:
            pass
        for alt in alt_colls:
            try:
                ac = vs.get_or_create_collection(alt)
                if ac.count():
                    collections.append(ac)
            except Exception:
                pass
        if not collections:
            return []

        # 🔴 池子要**远超**最终名额：原实现是「整章一个查询、直接取 top-8」，
        #    结果最像查询词的那一个小节把名额占光 —— 实测第6章 11 条里 6 条都是
        #    「4.1.1 决策名称」，其余小节一条范文都没有。没有范文的小节只能靠模型
        #    自由发挥，正是业主说的「让 LLM 自己去编造」。
        #    改成：先大池取样，再**按小节轮流取**（见下），让尽量多的小节都分到范文。
        _POOL = max(main_results * 8, 80)
        all_items = []
        for idx, col in enumerate(collections):
            limit = _POOL if idx == 0 else max(_POOL // 3, 20)
            res = self.vector_store.query(col, query_embedding, limit)
            if res.get("documents") and res["documents"][0]:
                for i in range(len(res["documents"][0])):
                    meta = res["metadatas"][0][i] if res.get("metadatas") else {}
                    all_items.append({
                        "document": res["documents"][0][i],
                        "metadata": meta,
                        "distance": res["distances"][0][i],
                        "collection": col.name,
                    })
        all_items.sort(key=lambda x: x["distance"])

        budget = main_results + alt_results * max(len(collections) - 1, 1)

        # 🔴 **主类型优先**：备选类型只用来**补位**，不和主类型平分名额。
        #    原先把所有库的片段混在一起轮流取，结果备选类型占比过高 ——
        #    实测第8章（主 `stakeholder_ref`，备选 `report_desc`）里混进了
        #    「1.3原则」这种**编制说明**的范文，与本章毫无关系，
        #    白占名额还把模型的注意力带偏（业主反馈「精度要调高」）。
        #    改成：先把主类型取完，取不满 budget 才去备选类型补。
        def _round_robin(items: List[Dict[str, Any]], cap: int,
                         picked: List[Dict[str, Any]], seen: set) -> int:
            """按 `section_title` 分桶后轮流取，返回本次取了几条。"""
            buckets: Dict[str, List[Dict[str, Any]]] = {}
            order: List[str] = []
            for it in items:
                sec = str((it.get("metadata") or {}).get("section_title", "") or "").strip()
                key = sec or f"__无小节__{it.get('collection', '')}"
                if key not in buckets:
                    buckets[key] = []
                    order.append(key)
                buckets[key].append(it)
            added, idx = 0, 0
            while len(picked) < cap:
                progressed = False
                for k in order:
                    if idx >= len(buckets[k]):
                        continue
                    cand = buckets[k][idx]
                    sig = str(cand.get("document", ""))[:200]
                    if sig in seen:              # 同一片段可能同时命中多个库
                        continue
                    seen.add(sig)
                    picked.append(cand)
                    added += 1
                    progressed = True
                    if len(picked) >= cap:
                        break
                if not progressed:
                    break
                idx += 1
            return added

        picked: List[Dict[str, Any]] = []
        seen_docs: set = set()
        for ci, col in enumerate(collections):
            pool = [it for it in all_items if it.get("collection") == col.name]
            pool.sort(key=lambda x: x["distance"])
            _round_robin(pool, budget, picked, seen_docs)
            if len(picked) >= budget:
                break
        return picked

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
        """Format retrieval results into structured context.

        🔴 小节对齐（B+C）：
        - C 元数据精排：本章编号开头的小节（如"1.5.2"）优先，有标题的次之，无标题最后；
          同小节内容稳定相邻（按小节标题分组），避免无关小节内容混在靠前位置。
        - B 注入分组：每条片段带【小节标题】前缀，agent 写哪个小节就能对号入座，防止写偏。
        """
        regulation_texts = []
        local_reg_texts = []    # 地方规范 (provincial/city-level standards)
        example_texts = []
        project_texts = []
        sources = []

        # ── C：按小节标题元数据精排 ──
        ch_prefix = f"{chapter_number}."

        def _sec_rank(item: Dict[str, Any]) -> int:
            sec = str((item.get("metadata") or {}).get("section_title", "") or "").strip()
            if sec.startswith(ch_prefix) or sec.startswith(f"第{chapter_number}章"):
                return 0  # 本章小节内容优先
            if sec:
                return 1  # 有标题（可能是跨章范本/条文）其次
            return 2      # 无标题最后

        ordered = sorted(
            items,
            key=lambda it: (_sec_rank(it), str((it.get("metadata") or {}).get("section_title", "") or "")),
        )

        for item in ordered:
            metadata = item.get("metadata", {})
            doc_type = metadata.get("document_type", "")
            doc_text = item.get("document", "")

            # 🔴 Smart reclassification: check filename for example/writing-guide indicators
            src_file = str(metadata.get("source_file", "")).lower()
            if any(kw in src_file for kw in ['example', '案例', '范文', 'template', '模板',
                                               'writing_guide', '写作', 'case_study']):
                doc_type = "example_report"

            # 🔴 `kb_*` 内容类型库**全是同类项目的报告正文**（范文），不是法规条文 ——
            #    它们的 metadata 用的是 `kb_type`，而上面读的是 `document_type`。
            #    读不到就掉进下面的 `else` 兜底分支，被整批塞进 `chapter_context`
            #    （prompt 里那一段的标题是「📖 评估依据（**必须引用**以下真实规范条款）」）。
            #    后果有两条，都是实测出来的：
            #      ① `example_context` 恒为 0 字 —— prompt 里「✍️ 同类型内容参考」
            #         那一整块（带「严禁照抄范文里的项目数据」约束）**永远不会出现**；
            #      ② 十几个别的项目的地名/文号（清江浦区黄码镇、运西村…）被当作
            #         「必须引用的规范条款」喂给模型，成了串数据的泄漏源
            #         —— 所以要靠 `_OLD_PROJECT_FRAGMENTS` 事后兜底。
            #    认 `kb_type` 即归位。
            if not doc_type and metadata.get("kb_type"):
                doc_type = "example_report"

            source = {
                "title": metadata.get("section_title", metadata.get("source_file", "")),
                "document_type": doc_type,
                "excerpt": doc_text[:200] + "..." if len(doc_text) > 200 else doc_text,
                "chapter_number": metadata.get("chapter_number"),
                "article_number": metadata.get("article_number", ""),
            }

            # ── B：带【小节标题】前缀，注入后按小节分组，agent 写哪节看哪节 ──
            sec = str(metadata.get("section_title", "") or "").strip()
            labeled = f"【{sec}】\n{doc_text}" if sec else doc_text

            if doc_type in ("regulation", "standard"):
                if len("\n".join(regulation_texts)) < 3000:
                    regulation_texts.append(labeled)
                    sources.append(source)
            elif doc_type == "local_regulation":
                if len("\n".join(local_reg_texts)) < 3000:
                    local_reg_texts.append(labeled)
                    sources.append(source)
            elif doc_type == "example_report":
                # 🔴 上限从 3000 提到 6000：分层检索保证「每个小节都有范文」，
                #    但 11 条片段加起来约 3500–5000 字，3000 的帽子会把后面几个小节
                #    的范文整条切掉，等于白分层。
                if len("\n".join(example_texts)) < 6000:
                    example_texts.append(labeled)
                    sources.append(source)
            elif doc_type == "project_material":
                if len("\n".join(project_texts)) < 2000:
                    project_texts.append(labeled)
                    sources.append(source)
            else:
                # Unknown type, add to regulation as fallback
                if len("\n".join(regulation_texts)) < 3000:
                    regulation_texts.append(labeled)
                    sources.append(source)

        return {
            "chapter_context": "\n\n---\n\n".join(regulation_texts),
            "local_regulation_context": "\n\n---\n\n".join(local_reg_texts),
            "example_context": "\n\n---\n\n".join(example_texts),
            "project_context": "\n\n---\n\n".join(project_texts),
            "sources": sources,
            "chapter_query": CHAPTER_QUERY_TEMPLATES.get(chapter_number, ""),
        }
