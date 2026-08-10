#!/usr/bin/env python3
"""Ingest Jiangsu-specific crawled stable-risk-assessment reports into the knowledge base."""
import sys, os, asyncio, re
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser

from dotenv import load_dotenv
for p in ['.env', 'backend/.env']:
    if os.path.exists(p): load_dotenv(p)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.chunker import ChineseReportChunker
from app.rag.vector_store import VectorStoreService
from app.rag.embedder import EmbedderService
from app.services.cleaning_pipeline import cleaning_pipeline
from app.database.knowledge_db import async_session
from sqlalchemy import text

JSU_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "crawled_reports" / "jiangsu"

class T(HTMLParser):
    def __init__(self): super().__init__(); self.t=[]; self.s=False
    def handle_data(self,d):
        if not self.s and d.strip(): self.t.append(d.strip())
    def handle_starttag(self,t,a):
        if t in ('script','style'): self.s=True
    def handle_endtag(self,t):
        if t in ('script','style'): self.s=False

def extract_html(p):
    ex = T()
    with open(p) as f: ex.feed(f.read())
    return '\n'.join(ex.t)

def extract_pdf(p):
    import fitz
    return '\n'.join(page.get_text() for page in fitz.open(p))

def extract_docx(p):
    try:
        from docx import Document
        return '\n'.join(para.text for para in Document(p).paragraphs)
    except:
        return ""

# ── Report definitions ──
REPORTS = [
    {
        "file": "huaian_shishibanfa.html",
        "extractor": extract_html,
        "title": "淮安市征地项目社会稳定风险评估实施办法（试行）淮政办发〔2012〕85号",
        "document_type": "政策法规",
        "domain": "stability",
        "region": "江苏省/淮安市",
        "year": "2012",
        "risk_tags": "征地程序/稳评办法/评估主体/备案审查/听证程序",
        "doc_category": "政策法规",
    },
    {
        "file": "huaian_dijia.html",
        "extractor": extract_html,
        "title": "淮安市所辖各县区征地区片综合地价调整社会稳定风险评估公示（2026年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/淮安市",
        "year": "2026",
        "risk_tags": "征地区片综合地价/补偿标准/清江浦区/安置补助费",
        "doc_category": "本地案例",
    },
    {
        "file": "liuhe_dijia.html",
        "extractor": extract_html,
        "title": "南京市六合区征地区片综合地价执行标准调整社会稳定风险评估公示（2025年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/南京市/六合区",
        "year": "2025",
        "risk_tags": "区片综合地价/补偿标准调整/征地补偿/南京标准",
        "doc_category": "本地案例",
    },
    {
        "file": "yizheng_muyuan.docx",
        "extractor": extract_docx,
        "title": "仪征市北郊墓园土地征收项目社会稳定风险评估公示（2024年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/扬州市/仪征市",
        "year": "2024",
        "risk_tags": "土地征收/公共事业用地/社会福利/征地程序",
        "doc_category": "本地案例",
    },
    {
        "file": "lianshui_caigou.pdf",
        "extractor": extract_pdf,
        "title": "涟水县2024年度一区三园土地征收社会稳定风险评估服务采购合同",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/淮安市/涟水县",
        "year": "2024",
        "risk_tags": "政府采购/稳评服务/合同范本/47元每亩/专家评审/政法委备案",
        "doc_category": "本地案例",
    },
    {
        "file": "jiangyan_qintong.html",
        "extractor": extract_html,
        "title": "泰州市姜堰区溱潼镇03街区4个地块土地征收社会稳定风险评估公示（2026年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/泰州市/姜堰区",
        "year": "2026",
        "risk_tags": "成片开发/土地征收/公众参与/泰州标准",
        "doc_category": "本地案例",
    },
    {
        "file": "suining_dijia.html",
        "extractor": extract_html,
        "title": "徐州市睢宁县征地区片综合地价及地上附着物补偿标准更新社会稳定风险评估公示（2025年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/徐州市/睢宁县",
        "year": "2025",
        "risk_tags": "地价更新/附着物补偿/徐州标准/征地补偿",
        "doc_category": "本地案例",
    },
    {
        "file": "wuxi_agencies.html",
        "extractor": extract_html,
        "title": "无锡市2026年度重大决策社会稳定风险评估第三方机构备案名录",
        "document_type": "工作指南",
        "domain": "stability",
        "region": "江苏省/无锡市",
        "year": "2026",
        "risk_tags": "第三方机构/备案名录/无锡稳评/评估机构资质",
        "doc_category": "工作指南",
    },
    {
        "file": "wuzhong_zhengdi.html",
        "extractor": extract_html,
        "title": "苏州市吴中区木渎镇尧峰村拟征地事项社会稳定风险评估公示（2026年）",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/苏州市/吴中区",
        "year": "2026",
        "risk_tags": "成片开发/土地征收/苏州案例/公众参与",
        "doc_category": "本地案例",
    },
    {
        "file": "huaian_G205.html",
        "extractor": extract_html,
        "title": "205国道淮安城区段工程社会稳定风险评估公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/淮安市",
        "year": "2026",
        "risk_tags": "国道改建/交通工程/征地拆迁/淮安案例",
        "doc_category": "本地案例",
    },
]


async def main():
    chunker = ChineseReportChunker(chunk_size=1000, chunk_overlap=150, max_chunk_size=4000)
    vs = VectorStoreService()
    embedder = EmbedderService()
    col = vs.get_or_create_collection("knowledge_base")
    print(f"Collection before: {col.count()}\n")

    async with async_session() as db:
        total_chunks = 0
        inserted = 0

        for r in REPORTS:
            filepath = JSU_DIR / r["file"]
            if not filepath.exists():
                print(f"⚠️  Skip: {r['file']}")
                continue
            raw_text = r["extractor"](str(filepath))
            if not raw_text or len(raw_text) < 50:
                print(f"⚠️  Empty: {r['file']}")
                continue

            print(f"📄 {r['title'][:60]}... ({len(raw_text)} chars)")

            # Clean
            cfg = cleaning_pipeline.get_default_config()
            cleaned = cleaning_pipeline.execute(raw_text, cfg)
            text_to_chunk = cleaned if len(cleaned.strip()) >= 20 else raw_text

            # Find existing or insert
            result = await db.execute(text(
                "SELECT id FROM knowledge_documents WHERE title = :t"), {"t": r["title"]})
            row = result.fetchone()
            doc_id = row[0] if row else None

            if doc_id:
                vs.remove_by_prefix(col, f"doc_{doc_id}")
            else:
                await db.execute(text("""INSERT INTO knowledge_documents
                    (title, document_type, file_path, file_type, domain, indexed_status,
                     chunk_count, collection_name, raw_text, cleaned_text, clean_status,
                     created_at, updated_at, is_active)
                    VALUES (:t, :dt, :fp, 'html', :dom, 'pending', 0,
                     'knowledge_base', :rt, :ct, 'cleaned', :now, :now, 1)"""),
                    {"t": r["title"], "dt": r["document_type"], "fp": r["file"],
                     "dom": r["domain"], "rt": raw_text[:80000], "ct": text_to_chunk[:80000],
                     "now": datetime.now(timezone.utc).isoformat()})
                await db.commit()
                result = await db.execute(text("SELECT last_insert_rowid()"))
                doc_id = result.scalar()
                print(f"  📝 New doc id={doc_id}")

            # Chunk
            meta = {
                "document_type": r["document_type"], "source_file": r["file"],
                "region": r["region"], "year": r["year"],
                "risk_tags": r["risk_tags"], "doc_category": r["doc_category"],
            }
            chunks = chunker.chunk_text(text_to_chunk, meta)
            chunks = chunker.inject_rag_tags(chunks)
            print(f"  📦 {len(chunks)} chunks")

            if not chunks: continue

            texts = [c.text for c in chunks]
            ids_list = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
            mds = [{
                "document_type": str(r["document_type"]),
                "source_file": str(r["title"]), "domain": "stability",
                "chapter_number": int(c.metadata.chapter_number or 0),
                "section_title": str(c.metadata.section_title or ""),
                "heading_level": int(c.metadata.heading_level or 0),
                "chunk_index": int(c.metadata.chunk_index or 0),
                "total_chunks": int(c.metadata.total_chunks or 0),
                "source_type": "file", "modality": "file", "image_summary": "",
            } for c in chunks]

            embeddings = await embedder.embed_texts(texts)
            if embeddings and embeddings[0]:
                vs.add_documents(col, ids_list, texts, embeddings, mds)
                total_chunks += len(chunks)
                await db.execute(text("""UPDATE knowledge_documents
                    SET chunk_count=:cc, indexed_status='indexed',
                    updated_at=:now WHERE id=:id"""),
                    {"cc": len(chunks), "now": datetime.now(timezone.utc).isoformat(), "id": doc_id})
                await db.commit()
                print(f"  ✅ Embedded {len(chunks)}")
            else:
                print(f"  ❌ Embed failed")

            inserted += 1

        print(f"\n{'='*60}")
        print(f"✅ Jiangsu reports: {inserted} docs, {total_chunks} chunks")
        print(f"   Collection: {col.count()} embeddings")
        print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
