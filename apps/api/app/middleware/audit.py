"""Audit context middleware.

Captures client IP address and User-Agent from incoming requests and stores
them in ``request.state`` for downstream audit logging.
"""

import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Extract audit-relevant metadata from each request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # SECURITY (H-06): Only trust X-Forwarded-For when the direct
        # connection comes from a trusted reverse proxy IP.
        direct_ip = request.client.host if request.client else None
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for and direct_ip:
            from app.config import settings

            trusted_proxies = {
                ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()
            }
            if trusted_proxies and direct_ip in trusted_proxies:
                candidate = forwarded_for.split(",")[0].strip()
                try:
                    ipaddress.ip_address(candidate)
                    request.state.client_ip = candidate
                except ValueError:
                    request.state.client_ip = direct_ip
            else:
                request.state.client_ip = direct_ip
        elif request.client:
            request.state.client_ip = request.client.host
        else:
            request.state.client_ip = None

        request.state.user_agent = request.headers.get("user-agent")

        response = await call_next(request)
        return response
