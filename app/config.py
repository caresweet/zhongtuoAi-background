"""Application configuration via pydantic-settings."""
import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    APP_NAME: str = "众拓AI智能生成报告"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"
    DATA_DIR: Path = BASE_DIR / "data"

    # 🔴 日志落盘目录 —— 排查问题用（前后端日志都留在这里）。
    #    不上生产也得有：用户界面**不给看运行日志**，出问题只能翻这个目录。
    #    可用 `LOG_DIR` 覆盖；`LOG_TO_FILE=false` 可关掉文件落盘。
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_TO_FILE: bool = True
    LOG_MAX_BYTES: int = 10 * 1024 * 1024   # 单文件 10MB
    LOG_BACKUP_COUNT: int = 5               # 保留 5 份轮转

    # Database — PostgreSQL in production, SQLite fallback for dev
    DATABASE_URL: str = ""  # postgresql+asyncpg://user:pass@host:5432/dbname
    KNOWLEDGE_DB_URL: str = ""
    HISTORY_DB_URL: str = ""
    AUTH_DB_URL: str = ""

    @property
    def knowledge_db_url(self) -> str:
        if self.KNOWLEDGE_DB_URL:
            return self.KNOWLEDGE_DB_URL
        if self.DATABASE_URL:
            return self.DATABASE_URL
        db_path = self.DATA_DIR / "knowledge_base.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def history_db_url(self) -> str:
        if self.HISTORY_DB_URL:
            return self.HISTORY_DB_URL
        if self.DATABASE_URL:
            return self.DATABASE_URL
        db_path = self.DATA_DIR / "history_reports.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def auth_db_url(self) -> str:
        if self.AUTH_DB_URL:
            return self.AUTH_DB_URL
        if self.DATABASE_URL:
            return self.DATABASE_URL
        db_path = self.DATA_DIR / "auth.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    # Redis (session store + cache)
    REDIS_URL: str = ""  # redis://:password@host:6379/0

    # JWT
    JWT_SECRET: str = ""  # MUST be set in production .env
    ALLOW_REGISTRATION: bool = True  # Set false to disable public registration

    @property
    def jwt_secret(self) -> str:
        """返回 JWT_SECRET；开发环境未配置时用**落盘的**随机密钥兜底。

        🔴 原先每次进程启动都现随机一个：`uvicorn --reload` 每改一行代码就重启，
           于是**所有已登录用户被踢下线**（表现是「点哪都跳登录页」），
           排查问题时极其误导。现在把首次生成的密钥写到 `data/.dev_jwt_secret`，
           重启后仍是同一把 —— 只影响本机开发，生产仍要求显式配置。
        """
        if self.JWT_SECRET:
            return self.JWT_SECRET
        # 生产：不兜底。见 app/security.py:jwt_secret() 的硬失败
        import secrets
        _dev_file = self.DATA_DIR / ".dev_jwt_secret"
        try:
            if _dev_file.exists():
                _v = _dev_file.read_text(encoding="utf-8").strip()
                if _v:
                    self.JWT_SECRET = _v
                    return _v
            self.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _v = secrets.token_hex(32)
            _dev_file.write_text(_v, encoding="utf-8")
            self.JWT_SECRET = _v
            return _v
        except Exception:
            # 落盘失败（只读文件系统等）→ 退回「本进程内有效」的旧行为
            import warnings
            warnings.warn(
                "JWT_SECRET is not set! Using a random key valid for this process only. "
                "Set JWT_SECRET in .env for production.",
                RuntimeWarning,
            )
            self.JWT_SECRET = secrets.token_hex(32)
            return self.JWT_SECRET

    # DeepSeek 官方 API（主文本模型）
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "deepseek-chat"
    ANTHROPIC_BASE_URL: str = ""

    # File Storage
    MAX_UPLOAD_SIZE_MB: int = 200
    MAX_IMAGE_SIZE_MB: int = 10
    MAX_PDF_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: list[str] = [".docx", ".doc", ".pdf"]
    ALLOWED_IMAGE_EXTENSIONS: list[str] = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]
    ALLOWED_ATTACHMENT_EXTENSIONS: list[str] = [
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".heif",  # images
        ".pdf",                                                  # PDF documents
        ".docx", ".doc",                                         # Word documents
        ".xlsx", ".xls", ".csv",                                 # Excel / CSV
        ".ppt", ".pptx",                                         # PowerPoint
        ".txt", ".md",                                           # text files
        ".zip", ".rar",                                          # archives
    ]
    MAX_ATTACHMENT_SIZE_MB: int = 100  # Max size for non-image, non-PDF attachments

    # Text LLM Service（Qwen3.7-Max via DashScope）
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL: str = "qwen3-max"

    # Agent Tool-Call 开关：开启后章节 agent 生成正文前可自主调用工具（RAG/勘测定界/评分）
    ENABLE_AGENT_TOOLCALL: bool = True

    # 🔴 章节正文生成**关掉模型思考链**（`thinking={"type":"disabled"}`）。
    #   主模型 deepseek-v4-pro 是推理模型，`reasoning_content` 与正文**共用**同一个
    #   `max_tokens` 预算。实测整本跑下来 110 次「预算被推理吃光」——
    #       LLM 空转（content=0 字，reasoning=13219 字，max_tokens=8000）
    #       LLM 空转（content=0 字，reasoning=23492 字，max_tokens=8000）
    #   每次都要服务内部再补救一轮（空转→关思考重跑／截断→预算翻倍重跑），
    #   单章因此从 40 秒涨到 160+ 秒，叠上外层 600s 超时 → 整章重来。
    #   写报告正文不需要长推理链，关掉即可。
    #   ⚠️ 保留开关是为了回滚：若某章确实依赖推理（如第12章评分口径），
    #      置 False 即恢复原行为。
    CHAPTER_DISABLE_THINKING: bool = True

    # 🔴 章节编写管线开关：
    #   "legacy"        → 旧的「LLM 自由写 markdown」路径（默认，保证可回滚）
    #   "template_fill" → 新的「模板填空 + 缺口补写 + 规则校验」路径
    CHAPTER_PIPELINE: str = "legacy"

    # 🔴 参照件照搬：第 1/2/4/5/7 章直接沿用人工成品原文（只替换地区/项目信息）。
    #   语料由 `scripts/build_reference_corpus.py` 离线生成到
    #   `data/reference_reports/<REFERENCE_CORPUS_ID>/`。
    REFERENCE_VERBATIM_ENABLED: bool = True
    REFERENCE_CORPUS_ID: str = "baimahu_wind_2_shenshen_v1"
    # 🔴 照搬源**按章分别指定**（见 build_reference_corpus.py 的 chapter_sources）：
    #   第1章 ← 知识库《洪泽社会稳定报告模板》（土地征收，体例为
    #           「（一）稳评理由 → 1. 2. 3.」三块，与业主口径一致）
    #   第2/5/7章 ← 白马湖参照件 PDF
    #   第4章未纳入：4.1 是风机参数、4.2.2 整段讲「度电成本优于火电/碳中和」，
    #          属**项目类型不同**（风电 vs 土地征收），字面替换解决不了。
    #   第1章为「照搬骨架 + agent 扩写」：结构体例（（一）稳评理由 → 1. 2. 3.）
    #   取自知识库洪泽模板，但**每条正文由 agent 结合事实卡与 RAG 写足**，
    #   故不列入纯照搬章。见 chapter_definitions 的第1章小节规范与专属提示词。
    REFERENCE_VERBATIM_CHAPTERS: str = "2,5,7"

    # 🔴 照搬章「模型改写」开关（业主 2026-09-16 定调：「除项目事实外全交给模型」）。
    #   True  —— 参照件只出**骨架**（标题 / 插图 / 系统表标记 / 法规白名单），
    #           正文行文由模型结合 RAG 与事实卡重写（见 services/reference_llm.py）。
    #   False —— 回到逐字照搬（零 LLM）。
    #   两条路径都保留：模型不可用或结构损坏时自动回退照搬稿。
    REFERENCE_LLM_REWRITE: bool = True

    # 模板槽位标注表中「未人工校订」时，槽位置信度降档阈值
    TEMPLATE_PROFILE_REVIEWED_CONFIDENCE: float = 0.9
    TEMPLATE_PROFILE_UNREVIEWED_CONFIDENCE: float = 0.6
    # OCR 单字段置信度下限（低于此值 → 生成 low_conf_ocr 缺口，值照写但标记待复核）
    OCR_FIELD_CONFIDENCE_MIN: float = 0.85

    # Vision model — Qwen-VL-Max via DashScope（图片识别 / OCR）
    VISION_MODEL: str = "qwen-vl-max"
    VISION_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VISION_API_KEY: str = ""

    # 缺图时的网络图片检索（可选；不配则自动降级为空候选）
    IMAGE_SEARCH_API_URL: str = ""
    IMAGE_SEARCH_API_KEY: str = ""

    # RAG / Embedding (DashScope Qwen embedding)
    EMBEDDING_API_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_API_KEY: str = ""
    CHROMA_PERSIST_DIR: str = ""

    @property
    def chroma_persist_dir(self) -> str:
        if self.CHROMA_PERSIST_DIR:
            return self.CHROMA_PERSIST_DIR
        return str(self.DATA_DIR / "chroma")

    # Session
    SESSION_TIMEOUT_HOURS: int = 24

    # CORS — restrict to actual domains in production
    CORS_ORIGINS: list[str] = ["*"]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        # 🔴 容忍无关环境变量：Docker 基础镜像自带 LANG/GPG_KEY，gunicorn 用 WORKERS，
        #    若 extra=forbid 会导致容器内 Settings 校验失败、后端无法启动。
        "extra": "ignore",
    }


settings = Settings()
