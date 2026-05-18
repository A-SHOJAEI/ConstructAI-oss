"""Shared client-IP resolution.

M-4: The SSO endpoint was reading ``request.client.host`` directly, which
lets an attacker behind a reverse proxy rotate spoofed IPs through
``X-Forwarded-For`` and bypass per-IP SSO state flood limits. This helper
centralizes the "trust XFF only when the direct connection comes from a
trusted proxy" rule used by the rate-limit middleware so SSO and future
callers get the same behavior for free.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request


def resolve_client_ip(request: Request) -> str:
    """Return the trusted client IP for ``request``.

    Honors ``X-Forwarded-For`` only when the direct peer is in
    ``TRUSTED_PROXY_IPS`` (config). Otherwise returns the direct peer.
    """
    from app.config import settings

    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    trusted = {ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()}
    if not trusted or peer not in trusted:
        return peer
    candidate = forwarded.split(",")[0].strip()
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        return peer
