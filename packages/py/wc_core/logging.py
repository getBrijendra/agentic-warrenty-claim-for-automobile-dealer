"""structlog JSON logging with a PII redaction processor."""
from __future__ import annotations

import logging
import re
import sys

import structlog

_PLATE = re.compile(r"\b(Dubai|Abu Dhabi|Sharjah)\s+[A-Z]{1,2}\s+\d{3,5}\b")
_PHONE = re.compile(r"\+?971[\s-]?\d{1,2}[\s-]?\d{3}[\s-]?\d{4}")


def _redact(_, __, event_dict):
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            v = _PLATE.sub("[plate]", v)
            v = _PHONE.sub("[phone]", v)
            event_dict[k] = v
    return event_dict


def setup_logging(service: str) -> structlog.BoundLogger:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    for noisy in ("httpx", "uvicorn.access", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )
    return structlog.get_logger().bind(service=service)
