"""Localhost-only middleware for /internal/* routes."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}


class InternalOnlyMiddleware(BaseHTTPMiddleware):
    """Block all /internal/* requests from non-localhost IPs. Returns 404 (not 403)."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/internal"):
            # Check for X-Forwarded-For (proxy/external) — reject if present
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            # Check actual client IP
            client_ip = request.client.host if request.client else None
            if client_ip not in LOCALHOST_IPS:
                return JSONResponse(status_code=404, content={"detail": "Not found"})

        return await call_next(request)
