# -*- coding: utf-8 -*-
"""把复垦范文灌进 kb_reclamation_* 类型库。

5 份样本的 level-1/level-2 章节结构完全一致（设计文档 §3），
按小节归并到语义类即可，不做稳评那种跨章语义归并。

用法：
    cd backend && python3 -m scripts.ingest_reclamation_corpus --files a.pdf b.docx ...
"""

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.rag.reclamation_kb_map import COLLECTIONS  # 单一事实源（Task 3）

# 小节标题 → 语义类。判据用「章号 + 关键词」而非完整标题 ——
# 5 份样本的小节命名有出入（3.2 下就有 生物资源/自然资源/植被 三种叫法）。
_SECTION_RULES = [
    (r"^1\.", "前言"),
    (r"^2\.", "编制总则"),
    (r"^3\.", "项目概况"),
    (r"^4\.[12]", "方向-损毁分析"),
    (r"^4\.3", "方向-生态影响"),
    (r"^4\.4", "方向-适宜性"),
    (r"^4\.[567]", "方向-水土平衡"),
    (r"^5\.", "措施"),
    (r"^6\.", "工程设计"),
    (r"^7\.", "投资估算"),
    (r"^[89]\.", "计划与效益"),
    (r"^10\.", "保障措施"),
]

_EXT_TO_READER = {
    ".docx": "docx", ".doc": "docx", ".pdf": "pdf", ".md": "text", ".txt": "text",
}


def section_key_for(heading: str) -> Optional[str]:
    """小节标题 → 语义类；认不出来返回 None（**不硬塞**，宁可漏不可错）。"""
    h = (heading or "").strip()
    for pat, key in _SECTION_RULES:
        if re.match(pat, h):
            return key
    return None


def _read_text(path: str) -> str:
    suffix = Path(path).suffix.lower()
    kind = _EXT_TO_READER.get(suffix)
    if kind == "docx":
        from app.services.file_service import file_service
        return file_service.extract_docx_text(path)
    if kind == "pdf":
        # 只取文本层，不跑 OCR —— 5 份样本都有文本层（设计文档 §2.1）
        from app.services.file_service import file_service
        return file_service.extract_pdf_text_fast(path)
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def build_chunks(path: str) -> List[Dict[str, Any]]:
    """一份范文 → 若干带 collection 归属的 chunk。

    切分用现成的 `ChineseReportChunker.chunk_markdown`（它按 X.Y 建 section，
    并把小节标题写进 `ChunkMetadata.section_title`）。
    """
    from app.rag.chunker import ChineseReportChunker

    text = _read_text(path)
    if not text or len(text.strip()) < 50:
        return []

    chunker = ChineseReportChunker(chunk_size=1000, chunk_overlap=120,
                                   max_chunk_size=4000)
    # chunk_markdown 只在**真 markdown**（有 # 标题）时才切出带 section_title 的小节；
    # 对 docx/pdf 抽出的裸文本，它会把全文塞进一个无标题 section（section_title 全空），
    # 那样 section_key_for 一律认不出来、整份被丢；且该结果非空，`or chunk_text` 兜不住。
    # 故无标题时回退到按 X.Y 识标题的 chunk_text —— 它才对裸文本有效。
    chunks = chunker.chunk_markdown(text)
    if not any((c.metadata.section_title or "").strip() for c in chunks):
        chunks = chunker.chunk_text(text)

    out: List[Dict[str, Any]] = []
    src_name = Path(path).name
    for c in chunks:
        m = c.metadata
        title = (m.section_title or "").strip()
        # 判据顺序很关键：本项目样本的 section_title **自带**「X.Y」编号
        # （如 '4.1.1 土地损毁环节与时序'）。若先拼「章号.标题」，会拼成 '4.4.1…'
        # 而误判到第 4.4 小节（全章 4 都被塞进 方向-适宜性）。故：
        #   1) 先按标题原样判；
        #   2) 标题未自带编号（如 '编制依据'）时，才用「章号.标题」补判；
        #   3) 最后退回只看章号。
        key = section_key_for(title)
        if key is None and m.chapter_number and not re.match(r"^\d", title):
            key = section_key_for(f"{m.chapter_number}.{title}")
        if key is None and m.chapter_number:
            key = section_key_for(f"{m.chapter_number}.")
        if key is None:
            continue            # 认不出来的小节不灌，避免污染类型库
        out.append({
            "text": c.text,
            "collection": COLLECTIONS[key],
            # ⚠️ ChunkMetadata 无「复垦方向」等字段，这里手工加；
            #    不要改共享的 ChunkMetadata 类（多传 key 会 TypeError）。
            "metadata": {
                "domain": "reclamation",
                "source_file": src_name,
                "chapter_number": int(m.chapter_number or 0),
                "section_title": str(m.section_title or ""),
                "heading_level": int(m.heading_level or 0),
                "chunk_index": int(m.chunk_index or 0),
                "total_chunks": int(m.total_chunks or 0),
            },
        })
    return out


async def ingest(files: List[str]) -> Dict[str, int]:
    """灌库：清复垦旧库 → 逐文件切段 → 批量 embed → 分库写入。"""
    from app.rag.embedder import EmbedderService
    from app.rag.vector_store import VectorStoreService

    embedder = EmbedderService()
    vs = VectorStoreService()
    stats: Dict[str, int] = {v: 0 for v in set(COLLECTIONS.values())}

    # 1) 清空复垦的库（幂等重灌）—— 只碰 kb_reclamation_*，不碰稳评/投标
    for coll in set(COLLECTIONS.values()):
        try:
            vs.delete_collection(coll)
        except Exception:
            pass

    # 2) 切段
    all_chunks: List[Dict[str, Any]] = []
    for f in files:
        all_chunks.extend(build_chunks(f))
    if not all_chunks:
        return stats

    # 3) 批量 embed
    embeddings = await embedder.embed_texts([c["text"] for c in all_chunks])
    if len(embeddings) != len(all_chunks):
        raise RuntimeError("embedding 数量与 chunk 数不符，灌库中止")

    # 4) 按 collection 分组写入
    by_coll: Dict[str, List[int]] = {}
    for i, c in enumerate(all_chunks):
        by_coll.setdefault(c["collection"], []).append(i)

    for coll_name, idxs in by_coll.items():
        coll = vs.get_or_create_collection(coll_name)
        slug = coll_name.replace("kb_reclamation_", "")
        ids = [f"reclamation_{slug}_chunk_{n}" for n in range(len(idxs))]
        vs.add_documents(
            coll,
            ids,
            [all_chunks[i]["text"] for i in idxs],
            [embeddings[i] for i in idxs],
            [all_chunks[i]["metadata"] for i in idxs],
        )
        stats[coll_name] = len(idxs)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True, help="复垦范文（docx/pdf/md）")
    args = ap.parse_args()
    stats = asyncio.run(ingest(args.files))
    for coll, n in sorted(stats.items()):
        print(f"{coll:<40} {n:>5} 块")


if __name__ == "__main__":
    main()
