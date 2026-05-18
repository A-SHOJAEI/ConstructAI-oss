"""Tests for the shared client-IP resolver (M-4).

Covers all branches of ``resolve_client_ip``:
- direct peer when no XFF header,
- direct peer when peer is not a trusted proxy (don't trust the XFF
  header — it's attacker-controlled in that case),
- forwarded value when the direct peer IS trusted,
- fall back to the direct peer when XFF carries something that isn't a
  valid IP address.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.utils.client_ip import resolve_client_ip


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only the attributes the
    helper reads."""

    def __init__(self, peer: str | None, headers: dict[str, str]):
        self.client = _FakeClient(peer) if peer else None
        self.headers = headers


@pytest.fixture
def trusted_proxy_settings():
    """Patch settings so 10.0.0.1 is treated as a trusted proxy."""
    with patch("app.config.settings.TRUSTED_PROXY_IPS", "10.0.0.1, 10.0.0.2"):
        yield


def test_no_xff_returns_direct_peer():
    req = _FakeRequest(peer="203.0.113.5", headers={})
    assert resolve_client_ip(req) == "203.0.113.5"


def test_xff_from_untrusted_peer_is_ignored(trusted_proxy_settings):
    """An untrusted peer can't override the IP via XFF — that would let
    attackers rotate through fake IPs to bypass per-IP rate limits."""
    req = _FakeRequest(
        peer="198.51.100.50",  # not in trusted list
        headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
    )
    assert resolve_client_ip(req) == "198.51.100.50"


def test_xff_from_trusted_peer_returns_first_value(trusted_proxy_settings):
    req = _FakeRequest(
        peer="10.0.0.1",
        headers={"x-forwarded-for": "203.0.113.10, 70.41.3.18"},
    )
    assert resolve_client_ip(req) == "203.0.113.10"


def test_xff_invalid_ip_falls_back_to_peer(trusted_proxy_settings):
    req = _FakeRequest(
        peer="10.0.0.1",
        headers={"x-forwarded-for": "not-an-ip"},
    )
    assert resolve_client_ip(req) == "10.0.0.1"


def test_no_trusted_proxies_configured_means_xff_ignored():
    """When TRUSTED_PROXY_IPS is empty, no peer is trusted to set XFF."""
    with patch("app.config.settings.TRUSTED_PROXY_IPS", ""):
        req = _FakeRequest(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.10"},
        )
        assert resolve_client_ip(req) == "10.0.0.1"


def test_request_without_client_returns_unknown():
    req = _FakeRequest(peer=None, headers={})
    assert resolve_client_ip(req) == "unknown"


def test_xff_ipv6_address_accepted(trusted_proxy_settings):
    req = _FakeRequest(
        peer="10.0.0.1",
        headers={"x-forwarded-for": "2001:db8::1"},
    )
    assert resolve_client_ip(req) == "2001:db8::1"


def test_trusted_list_ignores_blank_entries(trusted_proxy_settings):
    """The split(",") approach must tolerate trailing commas / extra whitespace
    without admitting an empty string into the trusted set (which would
    otherwise let any peer with empty .host bypass the check)."""
    with patch("app.config.settings.TRUSTED_PROXY_IPS", " 10.0.0.1 ,, "):
        req = _FakeRequest(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.10"},
        )
        assert resolve_client_ip(req) == "203.0.113.10"
