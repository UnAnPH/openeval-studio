"""Logging infrastructure and request ID formatting for OpenEval Studio."""

from __future__ import annotations

import logging
import sys

from server.request_context import get_request_id

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"


class RequestIdFilter(logging.Filter):
    """Logging filter ensuring record.request_id is populated from contextvar or '-'."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        return True


def setup_logger() -> logging.Logger:
    """Configure the 'openeval' logger hierarchy once with console INFO and correlation formatting."""
    logger = logging.getLogger("openeval")
    logger.disabled = False
    for name, log_obj in list(logging.root.manager.loggerDict.items()):
        if name.startswith("openeval") and isinstance(log_obj, logging.Logger):
            log_obj.disabled = False

    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(LOG_FORMAT)
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
        logger.addHandler(handler)
        logger.addFilter(RequestIdFilter())
    return logger
