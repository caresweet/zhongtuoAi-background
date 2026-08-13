#!/usr/bin/env python3
"""把已有报告按类型分类 → 清洗 → 物理隔离入库。

流程：
1. 扫描 seed_data/*.md + crawled_reports/ 递归（md/html/pdf/docx）
2. 分类：domain（稳评/招标）+ document_type（政策/标准/范文/模板/指南）+ region
3. 清洗（严格版：剥离公司信息/项目数据/老旧政策）
4. 按 domain 物理隔离入库（stability→knowledge_base，bidding→bidding_knowledge，新类型→kb_{id}）

用法：
    cd backend && python -m seed_data.reclassify_reports            # dry-run，只看分类
    cd backend && python -m seed_data.reclassify_reports --commit   # 实际入库
"""

import sys
import asyncio
import re
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.rag.chunker import ChineseReportChunker
from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService
from app.services.cleaning_pipeline import cleaning_pipeline
from app.services.self_learning.tags import (
    parse_metadata, infer_tags_from_filename, IsolationTags,
)
from app.services.file_service import file_service


# ── 分类规则 ────────────────────────────────────────────────────────────────

def classify_domain(filename: str) -> str:
    """按文件名判断报告类型（domain）。"""
    lower = filename.lower()
    if any(kw in lower for kw in ("招标", "投标", "标书", "bid", "tender", "评标", "中标")):
        return "bidding"
    return "stability"


def classify_doc_type(filename: str, text: str = "") -> str:
    """按文件名 + 内容判断文档性质（document_type）。仅作【】元数据缺失时的兜底。

    注意：company_info（公司固定资料）只能靠【固定资料】元数据识别，
    不在这里用"公司"关键词兜底——否则含"公司"二字的普通报告会被误判。
    """
    sample = f"{filename} {text[:200]}"
    if any(kw in sample for kw in ("标准", "规范", "DB32", "DB3206", "T4013", "T1091", "T1163")):
        return "standard"
    if any(kw in sample for kw in ("办法", "条例", "通知", "法规", "政策", "政规", "政发", "公告", "细则", "地价")):
        return "regulation"
    if any(kw in sample for kw in ("模板", "template", "muban", "范本")):
        return "template"
    if any(kw in sample for kw in ("指南", "guide", "指引", "手册", "方法论", "理论", "方法")):
        return "guide"
    if any(kw in sample for kw in ("报告", "report", "范文", "fanwen", "案例", "征收", "征地", "稳评")):
        return "example_report"
    return "example_report"


# ── 文本提取 ────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """简单 HTML → 纯文本（去标签，去脚本样式）。"""
    # 去 script/style
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.IGNORECASE | re.DOTALL)
    # 去标签
    html = re.sub(r'<[^>]+>', ' ', html)
    # 解码常见实体
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # 压缩空白
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n\s*\n+', '\n\n', html)
    return html.strip()


def extract_text(path: Path) -> str:
    """按扩展名提取文本。"""
    ext = path.suffix.lower()
    try:
        if ext == '.md' or ext == '.txt':
            return path.read_text(encoding='utf-8', errors='ignore')
        if ext == '.docx' or ext == '.doc':
            return file_service.extract_docx_text(str(path)) or ""
        if ext == '.pdf':
            return file_service.extract_pdf_text(str(path)) or ""
        if ext == '.html' or ext == '.htm':
            return _html_to_text(path.read_text(encoding='utf-8', errors='ignore'))
    except Exception as e:
        print(f"      ⚠ 提取失败 {path.name}: {e}")
    return ""


# ── 主流程 ──────────────────────────────────────────────────────────────────

async def reclassify(commit: bool = False):
    print("=" * 70)
    print("报告分类解析 → 清洗 → 物理隔离入库")
    print("=" * 70)

    seed_dir = Path(__file__).resolve().parent
    crawl_dir = seed_dir / "crawled_reports"

    # 收集所有文件
    files = sorted(seed_dir.glob("*.md"))  # 精选政策
    if crawl_dir.exists():
        files += sorted(crawl_dir.rglob("*"))  # 爬取报告递归

    # 过滤：只处理可解析的文件
    supported = {'.md', '.txt', '.docx', '.doc', '.pdf', '.html', '.htm'}
    files = [f for f in files if f.is_file() and f.suffix.lower() in supported]

    print(f"\n发现 {len(files)} 个文件\n")

    # 分类统计
    stats = {}
    items = []

    for f in files:
        text = extract_text(f)
        if not text or len(text.strip()) < 30:
            continue

        # 分类：优先【】元数据，文件名推断兜底
        domain = classify_domain(f.name)
        meta = parse_metadata(text)
        if meta:
            doc_type = meta.document_type          # 【】元数据里的类型（最准）
            region = meta.region
        else:
            doc_type = classify_doc_type(f.name, text)
            region = infer_tags_from_filename(f.name, doc_type).region

        # 清洗（严格版）
        cleaned = cleaning_pipeline.execute(text, cleaning_pipeline.get_default_config())

        items.append({
            "file": f.name,
            "domain": domain,
            "doc_type": doc_type,
            "region": region,
            "raw_len": len(text),
            "clean_len": len(cleaned.strip()),
            "cleaned": cleaned,
        })

        key = f"{domain}/{doc_type}/{region}"
        stats[key] = stats.get(key, 0) + 1
        print(f"  [{domain}/{doc_type}/{region or '?'}] {f.name}  ({len(text)}→{len(cleaned.strip())}字)")

    # 统计汇总
    print("\n" + "=" * 70)
    print("分类统计：")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count} 份")
    print(f"  共 {len(items)} 份有效报告")

    if not commit:
        print("\n[dry-run] 未入库。确认分类正确后，加 --commit 参数实际入库。")
        return

    # ── 实际入库（物理隔离）──
    print("\n开始入库（物理隔离：按 domain 存对应 collection）...")
    embedder = EmbedderService()
    vector_store = VectorStoreService()
    chunker = ChineseReportChunker()

    # 🔴 清空旧的 collection（保留 session_* 临时会话材料）
    _clear_old_collections(vector_store)

    total = 0
    for item in items:
        domain = item["domain"]
        text = item["cleaned"]
        if len(text.strip()) < 50:
            continue

        chunks = chunker.chunk_markdown(text) or chunker.chunk_text(text)
        chunk_texts = [c.text for c in chunks]
        if not chunk_texts:
            continue

        embeddings = await embedder.embed_texts(chunk_texts)
        if not embeddings:
            print(f"  ✗ {item['file']}: embedding failed")
            continue

        # 🔴 物理隔离：按 domain 存对应 collection
        collection_name = vector_store.get_domain_collection_name(domain)
        prefix = f"seed_{item['file'].replace('.', '_')}"
        vector_store.add_document_to_global(
            chunks=[{"text": t, "embedding": e, "metadata": {
                "document_type": item["doc_type"],
                "region": item["region"],
                "tenant_id": "public",
                "source_file": item["file"],
            }} for t, e in zip(chunk_texts, embeddings)],
            doc_id_prefix=prefix,
            domain=domain,
        )
        total += len(chunk_texts)
        print(f"  ✓ [{domain}/{item['doc_type']}] {item['file']} → {collection_name}: {len(chunk_texts)} chunks")

    print("\n" + "=" * 70)
    print(f"完成。共入库 {total} chunks，按 domain 物理隔离。")


def _clear_old_collections(vector_store: VectorStoreService):
    """清空旧的稳评/新类型 collection。

    只清空 knowledge_base + kb_* 前缀（本脚本处理的报告类型）。
    保留：
    - session_* 临时会话材料
    - bidding_knowledge（招标数据有独立来源，不在 crawled_reports 里）
    """
    print("\n清空旧的 collection...")
    try:
        collections = vector_store.list_collections()
    except Exception as e:
        print(f"  ⚠ 无法列出 collections: {e}")
        return

    for name in collections:
        # 保留 session_* 和 bidding_knowledge
        if name.startswith(VectorStoreService.SESSION_PREFIX):
            continue
        if name == "bidding_knowledge":
            print(f"  ⊘ 保留 {name}（招标独立来源）")
            continue
        try:
            vector_store.delete_collection(name)
            print(f"  ✓ 已删除 {name}")
        except Exception as e:
            print(f"  ⚠ 删除 {name} 失败: {e}")


if __name__ == "__main__":
    asyncio.run(reclassify(commit="--commit" in sys.argv))
