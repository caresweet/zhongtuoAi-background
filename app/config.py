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
        """Return JWT_SECRET, generating a dev-only warning fallback if unset."""
        if self.JWT_SECRET:
            return self.JWT_SECRET
        import warnings
        warnings.warn(
            "JWT_SECRET is not set! Using a random key valid for this process only. "
            "Set JWT_SECRET in .env for production.",
            RuntimeWarning,
        )
        import secrets
        # Cache the generated key so tokens remain valid within the process lifetime
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

    # Vision model — Qwen-VL-Max via DashScope（图片识别 / OCR）
    VISION_MODEL: str = "qwen-vl-max"
    VISION_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VISION_API_KEY: str = ""

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
    }


settings = Settings()
