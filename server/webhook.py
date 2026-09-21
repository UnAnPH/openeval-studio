"""Optional outbound webhook for Watcher block/escalate events (incident notification)."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger("openeval.webhook")


def emit_watcher_webhook(event: str, payload: dict[str, Any]) -> None:
    """POST JSON to WATCHER_WEBHOOK_URL if configured. Never raises."""
    url = os.environ.get("WATCHER_WEBHOOK_URL", "").strip()
    if not url:
        return
    body = {"event": event, **payload}
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            _ = resp.read()
    except Exception as err:
        logger.warning("Watcher webhook failed: %s", err)
