import logging.config
import os
import sys
from copy import copy
from typing import ClassVar

from .config import settings

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
COLOR_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class ColorFormatter(logging.Formatter):
    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[2;36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    DIM = "\033[2m"
    RESET = "\033[0m"

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return f"{self.DIM}{super().formatTime(record, datefmt)}{self.RESET}"

    def format(self, record: logging.LogRecord) -> str:
        record = copy(record)
        color = self.COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        record.name = f"{self.DIM}{record.name}{self.RESET}"
        return super().format(record)


class DropASGIExceptionEcho(logging.Filter):
    MESSAGE = "Exception in ASGI application"

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.getMessage().startswith(self.MESSAGE):
            return True
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, Exception)


def setup_logging() -> None:

    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": FORMAT},
                "color": {"()": ColorFormatter, "fmt": COLOR_FORMAT},
            },
            "filters": {
                "drop_asgi_exception_echo": {"()": DropASGIExceptionEcho},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "color" if color else "standard",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": settings.log_level.upper(),
            },
            "loggers": {
                # uvicorn ships its own handlers and propagate=False. Clearing
                # both routes its startup and access lines through ours. Pinned
                # at INFO so LOG_LEVEL=DEBUG surfaces our logs without also
                # unleashing uvicorn's protocol-level chatter.
                "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
                "uvicorn.error": {
                    "handlers": [],
                    "level": "INFO",
                    "propagate": True,
                    "filters": ["drop_asgi_exception_echo"],
                },
                "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": True},
            },
        }
    )
