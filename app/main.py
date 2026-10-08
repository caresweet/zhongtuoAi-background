"""FastAPI application entry point — 众拓AI智能生成报告."""
import logging
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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize databases on startup."""
    # Startup — init all databases
    await init_knowledge_db()
    await init_history_db()

    # Init auth database (users table) + 多租户迁移
    try:
        from app.database.auth_db import init_auth_db
        await init_auth_db()
    except Exception as e:
        logger.error(f"Auth DB init failed — auth endpoints may not work: {e}")

    # 🔴 报告归属回填必须在 `init_auth_db` **之后**：它要先从 auth 库读管理员 id，
    #    再对 history 库发单表 UPDATE（开发机三个库是独立 SQLite，跨库 JOIN 跑不了）。
    try:
        from app.database.history_db import backfill_report_owners
        await backfill_report_owners()
    except Exception as e:
        logger.warning(f"报告归属回填失败（不影响启动）：{e}")
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
                if not _fp:
                    continue
                # 🔴 登记了文件但磁盘上没有 → 明确告警，不再静默 pass。
                #    数据库是从别处拷来的、或文件被清理过时，会出现这种"幽灵模板"：
                #    前端列表里能看到、点生成才失败。这里启动时就报出来。
                try:
                    _abs = _fs.get_absolute_path(_fp)
                except Exception:
                    _abs = None
                if _abs is None or not _abs.exists():
                    logger.warning(
                        f"⚠️ 模板文件缺失：id={_t.id}「{_t.name}」→ {_fp}（该模板会在生成时报错，"
                        f"请补回文件或置 is_active=0）")
                    continue
                if _fp and _fp not in _cleaning_cache:
                    try:
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

    # ── 不清理用户上传图片/材料：storage/images 存放项目照片与 PDF 抽取图（附件要用），
    #    绝不能按时间自动删除（历史教训：>24h 清理导致现场照片永久丢失）。仅清理临时目录。

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
# ═══════════════════════════════════════════════════════════════
# 路由装配 —— **登记表式**，默认要求登录
# ═══════════════════════════════════════════════════════════════
# 🔴 为什么改成这样
#     原先是一串裸 `app.include_router(x.router)`，加不加鉴权全靠人记得 ——
#     实测结果：13 个 router 里**只有 auth 自己带鉴权**，报告列表全量返回、
#     `/reports/{id}/download` 知道 id 就能下别人的报告。
#     现在**要公开必须显式写进 PUBLIC_ROUTERS**，其余一律挂 `get_current_user`；
#     `tests/test_auth_coverage.py` 会断言「除白名单外每条路由都挂了鉴权」，
#     以后新增 router 忘了挂会**直接测试失败**，不靠纪律。
#
#     公开白名单**只能放真正匿名的**。动它要同步改那个测试里的白名单。
from fastapi import Depends
from app.deps import get_current_user, require_admin, require_staff
from app.models.user import User
from app.routers import knowledge, report, history, dashboard, auth, knowledge_chat, learning, bidding_screening, ocr_review, check_rules, approval, public_stats, company, admin, client_logs

PUBLIC_ROUTERS = [
    auth.router,                   # /login /register 公开；/me /refresh 自身要求登录
    public_stats.router,           # 门户统计（首屏用）
    knowledge_chat.public_router,  # 门户客服问答（匿名**只读**：不落会话、不学习）
]

# 🔴 **WebSocket 路由** —— 绝不能挂 HTTP 鉴权依赖，单独一组装配。
#    为什么单独列：浏览器 WebSocket API **不支持自定义请求头**，握手时带不了
#    `Authorization`，一旦套上 `Depends(get_current_user)` 就是**必然 401**——
#    前端表现为 `onerror`：「There was a bad response from the server」。
#    它们的鉴权在路由内部完成：`authenticate_websocket()` 读 `?token=` query 参数，
#    不合法就 `close(4001)`。所以放进这一组**不等于放空鉴权**，
#    反而比 HTTP 更严（未认证时连「这个会话存不存在」都问不出来）。
WS_ROUTERS = [
    report.ws_router,              # /api/v1/reports/ws/{session_id}
]

USER_ROUTERS = [
    report.router, history.router, dashboard.router,
    # 🔴 company 必须在这里（**登录即可**）：公司档案与人员库是每个用户自己的数据，
    #    放管理员组普通用户就选不了报告人员了。
    company.router,
    # 前端日志上报（登录即可）：前端只进控制台的日志统一落到 logs/frontend.log
    client_logs.router,
]

# 🔴 **内部工具** —— 业主口径：「用户不能看到知识库」。
#    这里的判据是 `require_staff` = **管理员 或 挂内部公司的同事**，
#    不是「仅 role=admin」：我们自己公司的员工本来就要用知识库/校对/审批，
#    而外部客户账号一律 403。前端侧边栏用同一个口径
#    （`authStore.isStaff`，依据 `user.company_is_internal`）—— 两边必须逐条对齐。
ADMIN_ROUTERS = [
    knowledge.router, knowledge_chat.router,      # 知识库 + 知识问答（审核/学习）
    learning.router, bidding_screening.router,
    ocr_review.router, check_rules.router, approval.router,
    admin.router,                                 # 账号/公司管理
]

for _r in PUBLIC_ROUTERS:
    app.include_router(_r)
for _r in WS_ROUTERS:
    app.include_router(_r)   # ⚠️ 故意不挂 get_current_user —— 见上方 WS_ROUTERS 注释
for _r in USER_ROUTERS:
    app.include_router(_r, dependencies=[Depends(get_current_user)])
for _r in ADMIN_ROUTERS:
    app.include_router(_r, dependencies=[Depends(require_staff)])



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
async def serve_file(file_path: str, user: User = Depends(get_current_user)):
    """Serve stored files (images, generated reports, etc.).

    🔴 这个口子原先**完全免鉴权** —— 拼路径就能读到任何上传件与任何生成的报告
       （多租户下等于把别家的营业执照图直接交出去）。现在至少要求登录，
       并挡住路径穿越。按归属（文件属于哪个用户/公司）的隔离见后续阶段。
    """
    # 🔴 这个口子原先**完全免鉴权** —— 拼路径就能读到任何上传件与任何生成的报告
    #    （多租户下等于把别家的营业执照图直接交出去）。现在两道：
    #      ① 挡住路径穿越（`..` / 绝对路径都不能逃出 STORAGE_DIR）；
    #      ② 按归属放行 —— 文件必须属于当前用户名下的报告或会话（见 file_access.py）。
    _root = settings.STORAGE_DIR.resolve()
    full_path = (_root / file_path).resolve()
    if not full_path.is_relative_to(_root):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="非法路径")

    from app.services.file_access import can_read_file
    if not await can_read_file(file_path, user):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文件不存在")

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
async def health_deep(_: User = Depends(require_admin)):
    """Deep health check — validates all dependencies are reachable.

    Returns per-dependency status so monitoring tools can alert on
    specific failures without false positives from unrelated deps.

    🔴 仅管理员：它会吐出模型 base_url、熔断状态、ChromaDB/Redis 拓扑 ——
       这些是**内部运维信息**，不适合匿名暴露。
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
