"""Internal-only middleware for /internal/* routes.

Authenticates via shared secret read from /secrets/internal-secret.
"""

import logging
import os
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

INTERNAL_SECRET_PATH = os.environ.get("INTERNAL_SECRET_PATH", "/secrets/internal-secret")


def _load_secret() -> str | None:
    try:
        return Path(INTERNAL_SECRET_PATH).read_text().strip()
    except FileNotFoundError:
        return None


class InternalOnlyMiddleware(BaseHTTPMiddleware):
    """Block all /internal/* requests without a valid X-Internal-Secret header."""

    def __init__(self, app):
        super().__init__(app)
        self._secret = _load_secret()

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/internal"):
            if not self._secret:
                logger.info("[INTERNAL-MW] rejected %s: no secret configured", request.url.path)
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            provided = request.headers.get("X-Internal-Secret")
            if provided != self._secret:
                logger.info("[INTERNAL-MW] rejected %s: secret mismatch (got %s chars)", request.url.path, len(provided) if provided else 0)
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            logger.info("[INTERNAL-MW] passed %s from %s", request.url.path, request.client.host if request.client else "unknown")

        return await call_next(request)
