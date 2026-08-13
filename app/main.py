"""FastAPI application entry point — 众拓AI智能生成报告."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.config import settings

# ── Structured logging (JSON in production, human-readable in DEBUG) ──────
from app.utils.logging_config import setup_logging
setup_logging(debug=settings.DEBUG)

from app.database.knowledge_db import init_knowledge_db
from app.database.history_db import init_history_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize databases on startup."""
    # Startup — init all databases
    await init_knowledge_db()
    await init_history_db()

    # Init auth database (users table)
    try:
        from app.database.auth_db import init_auth_db
        await init_auth_db()
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Auth DB init failed — auth endpoints may not work"
        )
    # Ensure storage directories exist
    settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.STORAGE_DIR / "templates").mkdir(parents=True, exist_ok=True)
    (settings.STORAGE_DIR / "examples").mkdir(parents=True, exist_ok=True)
    (settings.STORAGE_DIR / "generated").mkdir(parents=True, exist_ok=True)
    (settings.STORAGE_DIR / "temp").mkdir(parents=True, exist_ok=True)
    (settings.STORAGE_DIR / "images").mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-warm cleaning cache: extract text from all active templates & docs
    try:
        import asyncio as _asyncio
        from app.database.knowledge_db import async_session as _async_session
        from app.models.knowledge import KnowledgeDocument, Template
        from app.services.file_service import file_service as _fs
        from app.services.cleaning_pipeline import text_cache as _cleaning_cache
        from sqlalchemy import select as _select

        async with _async_session() as _db:
            # Preload templates
            _tpls = (await _db.execute(
                _select(Template).where(Template.is_active == True)
            )).scalars().all()
            for _t in _tpls:
                _fp = _t.template_file_path
                if _fp and _fp not in _cleaning_cache:
                    try:
                        _abs = _fs.get_absolute_path(_fp)
                        if _abs.suffix.lower() in ('.docx', '.doc'):
                            _cleaning_cache[_fp] = _fs.extract_docx_text(str(_abs))
                    except Exception:
                        pass

            # Preload knowledge docs
            _docs = (await _db.execute(
                _select(KnowledgeDocument).where(KnowledgeDocument.is_active == True)
            )).scalars().all()
            for _d in _docs:
                _fp = _d.file_path
                if _fp and _d.raw_text and _fp not in _cleaning_cache:
                    _cleaning_cache[_fp] = _d.raw_text
    except Exception:
        pass  # Non-critical — cache is best-effort

    # Clean orphaned images older than 24h (not tied to any session)
    import time, os
    images_dir = settings.STORAGE_DIR / "images"
    if images_dir.exists():
        now = time.time()
        cleaned = 0
        for f in images_dir.iterdir():
            if f.is_file():
                age_h = (now - f.stat().st_mtime) / 3600
                if age_h > 24:
                    try:
                        f.unlink()
                        cleaned += 1
                    except Exception:
                        pass
        if cleaned:
            import logging
            logging.getLogger(__name__).info(
                f"Startup cleanup: removed {cleaned} orphaned image files (>24h old)"
            )

    # Clean orphaned ChromaDB session collections (abandoned sessions)
    try:
        from app.rag.vector_store import VectorStoreService
        vs = VectorStoreService()
        collections = vs.list_collections()
        session_prefix = "session_"
        orphaned = 0
        for coll_name in collections:
            if coll_name.startswith(session_prefix):
                try:
                    vs.delete_collection(coll_name)
                    orphaned += 1
                except Exception:
                    pass
        if orphaned:
            import logging
            logging.getLogger(__name__).info(
                f"Startup cleanup: removed {orphaned} orphaned ChromaDB session collections"
            )
    except Exception:
        pass  # Non-critical — ChromaDB might not be available yet

    # Refresh learning hints on startup
    try:
        from app.services.master_orchestrator import refresh_learning_hints
        await refresh_learning_hints("stability")
    except Exception:
        pass

    yield
    # Shutdown: cleanup temp files
    import shutil
    temp_dir = settings.STORAGE_DIR / "temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
        temp_dir.mkdir()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI智能报告生成系统 — 基于Claude AI的对话式报告生成平台",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZip compression for large JSON responses
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Register routers
from app.routers import knowledge, report, history, dashboard, auth, knowledge_chat, learning

app.include_router(knowledge.router)
app.include_router(report.router)
app.include_router(history.router)
app.include_router(dashboard.router)
app.include_router(auth.router)
app.include_router(knowledge_chat.router)
app.include_router(learning.router)


@app.get("/")
async def root():
    """Health check."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
    }


# MIME type map — Python's mimetypes module doesn't know .docx/.xlsx etc.
_MIME_MAP = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
}


@app.get("/api/v1/files/{file_path:path}")
async def serve_file(file_path: str):
    """Serve stored files (images, generated reports, etc.)."""
    full_path = settings.STORAGE_DIR / file_path
    if not full_path.exists() or not full_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = full_path.suffix.lower()
    media_type = _MIME_MAP.get(suffix, "application/octet-stream")
    # Force download for binary formats the browser shouldn't render inline
    return FileResponse(
        str(full_path),
        media_type=media_type,
        filename=full_path.name,
    )


@app.get("/api/v1/health")
async def health():
    """Basic health check — process is alive."""
    return {"status": "healthy", "version": settings.APP_VERSION}


@app.get("/api/v1/health/deep")
async def health_deep():
    """Deep health check — validates all dependencies are reachable.

    Returns per-dependency status so monitoring tools can alert on
    specific failures without false positives from unrelated deps.
    """
    import time as _time
    checks = {}

    # 1. Database
    t0 = _time.time()
    try:
        from app.database.knowledge_db import engine as kb_engine
        async with kb_engine.connect() as conn:
            await conn.execute(__import__('sqlalchemy').text("SELECT 1"))
        checks["database"] = {"status": "ok", "latency_ms": round((_time.time() - t0) * 1000, 1)}
    except Exception as e:
        checks["database"] = {"status": "down", "error": str(e)[:200]}

    # 2. Redis
    t0 = _time.time()
    try:
        from app.config import settings as _s
        if _s.REDIS_URL:
            import redis.asyncio as _aioredis
            r = _aioredis.from_url(_s.REDIS_URL, socket_connect_timeout=3)
            await r.ping()
            await r.aclose()
            checks["redis"] = {"status": "ok", "latency_ms": round((_time.time() - t0) * 1000, 1)}
        else:
            checks["redis"] = {"status": "skipped", "reason": "not configured"}
    except Exception as e:
        checks["redis"] = {"status": "down", "error": str(e)[:200]}

    # 3. LLM API
    t0 = _time.time()
    try:
        from app.services.llm_service import llm_service as _llm
        if _llm.is_available:
            checks["llm_api"] = {
                "status": "ok",
                "model": _llm.model,
                "base_url": _llm.base_url,
                "circuit_open": _llm._circuit_open,
                "consecutive_failures": _llm._failure_count,
                "latency_ms": round((_time.time() - t0) * 1000, 1),
            }
        else:
            checks["llm_api"] = {"status": "skipped", "reason": "no API key configured"}
    except Exception as e:
        checks["llm_api"] = {"status": "down", "error": str(e)[:200]}

    # 4. ChromaDB
    t0 = _time.time()
    try:
        from app.rag.vector_store import VectorStoreService
        vs = VectorStoreService()
        vs.list_collections()
        checks["chromadb"] = {"status": "ok", "latency_ms": round((_time.time() - t0) * 1000, 1)}
    except Exception as e:
        checks["chromadb"] = {"status": "down", "error": str(e)[:200]}

    # Overall status
    all_ok = all(
        c.get("status") in ("ok", "skipped")
        for c in checks.values()
    )
    return {
        "status": "healthy" if all_ok else "degraded",
        "version": settings.APP_VERSION,
        "checks": checks,
    }
