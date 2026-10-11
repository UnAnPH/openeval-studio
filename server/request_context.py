"""Request context and correlation tracking for OpenEval Studio."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the active correlation request ID from contextvar, defaulting to '-'."""
    return request_id_ctx.get()


def set_request_id(req_id: str) -> None:
    """Explicitly assign the correlation request ID in contextvar."""
    request_id_ctx.set(req_id)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware extracting or generating X-Request-ID, setting context, and echoing the header."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_id = request.headers.get("x-request-id")
        req_id = incoming_id.strip() if incoming_id and incoming_id.strip() else uuid.uuid4().hex
        token = request_id_ctx.set(req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            request_id_ctx.reset(token)
