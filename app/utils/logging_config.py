"""Structured JSON logging configuration for production.

Usage (in main.py or app startup):
    from app.utils.logging_config import setup_logging
    setup_logging()
"""
import logging
import json
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON log formatter — machine-parseable, Docker/grep friendly."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include extra fields added via `extra={...}` in log calls
        extra_fields = {
            k: v for k, v in record.__dict__.items()
            if k not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName", "taskName",
            }
        }
        if extra_fields:
            log_entry["extra"] = extra_fields

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(debug: bool = False):
    """Configure root logger.

    两路输出：
      · **stdout** —— 容器/开发终端看，DEBUG 人类可读、生产 JSON（机器可解析）。
      · **文件**   —— `backend/logs/backend.log`（轮转 10MB×5）。
        🔴 界面上**不给用户看运行日志**（见 `routers/report.py` 的 workflow/status
           与前端 AssistantView 的日志面板），出问题只能翻这个目录 ——
           所以文件日志不是可选项。
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if debug:
        # Human-readable for development
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))
    else:
        # JSON for production (structured logging)
        handler.setFormatter(JsonFormatter())

    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    _add_file_handler(root, debug)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore",
                  "chromadb", "urllib3", "botocore", "asyncio",
                  "pdfminer", "pdfplumber", "aiosqlite", "pypdf", "fitz"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Keep uvicorn.error at INFO so startup/shutdown are visible
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        "Logging configured", extra={"mode": "debug" if debug else "json"}
    )


def _add_file_handler(root: logging.Logger, debug: bool) -> None:
    """把日志同时写到 `settings.LOG_DIR/backend.log`（轮转）。

    🔴 文件里**始终用人类可读格式**（不用 JSON）：排查问题时是人在翻，
       JSON 单行几万字符反而不便。也不受 `DEBUG` 影响地记 INFO 以上。
    """
    try:
        from logging.handlers import RotatingFileHandler

        from app.config import settings

        if not getattr(settings, "LOG_TO_FILE", True):
            return
        log_dir = settings.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            str(log_dir / "backend.log"),
            maxBytes=getattr(settings, "LOG_MAX_BYTES", 10 * 1024 * 1024),
            backupCount=getattr(settings, "LOG_BACKUP_COUNT", 5),
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        fh.setLevel(logging.DEBUG if debug else logging.INFO)
        root.addHandler(fh)
    except Exception as e:
        # 落盘失败（只读文件系统等）不能拖垮启动，但要说清楚
        logging.getLogger(__name__).warning(f"文件日志初始化失败（仅 stdout 输出）: {e}")


def log_frontend(lines: list[str]) -> None:
    """把前端上报的日志追加到 `logs/frontend.log`。

    🔴 前端日志原先**只进浏览器控制台**，用户那边一报错我们什么都拿不到。
       现在由前端 `utils/logger.js` 批量上报到这里，与后端日志同目录，便于对照时间线。
    单独用一个文件是为了不和后端日志混在一起（两边的时间戳含义不同）。
    """
    try:
        from app.config import settings

        if not getattr(settings, "LOG_TO_FILE", True):
            return
        log_dir = settings.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "frontend.log", "a", encoding="utf-8") as f:
            for line in lines:
                f.write(str(line).rstrip() + "\n")
    except Exception as e:
        logging.getLogger(__name__).warning(f"前端日志落盘失败: {e}")
