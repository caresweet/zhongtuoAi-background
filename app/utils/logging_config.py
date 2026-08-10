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
    """Configure root logger with JSON output to stdout.

    In DEBUG mode, uses a human-readable format instead.
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

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore",
                  "chromadb", "urllib3", "botocore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Keep uvicorn.error at INFO so startup/shutdown are visible
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        "Logging configured", extra={"mode": "debug" if debug else "json"}
    )
