"""Tests for the notification service SSRF protection.

``_validate_webhook_url`` is the gate for every webhook POST. SSRF is
the canonical worry: an attacker who controls a NotificationPreference
record could try to point it at AWS metadata (169.254.169.254), an
internal RFC1918 service, or 127.0.0.1 to hit a local-only API.

These tests pin every reject branch + the dev-mode loopback bypass +
the SMTP-not-configured short-circuit.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from app.services.agents import notification_service as svc

# =========================================================================
# _validate_webhook_url — protocol gate
# =========================================================================


def test_https_url_to_public_host_allowed():
    """Sanity: an https:// URL whose hostname resolves to a public IP
    must pass."""
    with patch.object(svc, "_DEV_MODE", False):
        # Force the resolver to return a public IP (8.8.8.8) so we don't
        # depend on DNS in CI:
        with patch.object(svc.socket, "getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
            ]
            out = svc._validate_webhook_url("https://hooks.example.com/abc")
            assert out == "https://hooks.example.com/abc"


def test_http_rejected_in_production():
    """[SSRF] Plain HTTP must be rejected in prod — encrypted only."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="Only HTTPS is allowed"):
            svc._validate_webhook_url("http://hooks.example.com/abc")


def test_http_to_localhost_allowed_in_dev_mode():
    with patch.object(svc, "_DEV_MODE", True):
        out = svc._validate_webhook_url("http://localhost:8080/hook")
        # Returns the URL unchanged when allowed:
        assert "localhost" in out


def test_http_to_127_allowed_in_dev_mode():
    with patch.object(svc, "_DEV_MODE", True):
        out = svc._validate_webhook_url("http://127.0.0.1:8080/hook")
        assert "127.0.0.1" in out


def test_ftp_scheme_rejected():
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="Unsupported webhook protocol"):
            svc._validate_webhook_url("ftp://example.com/path")


def test_file_scheme_rejected():
    """[security] file:// URLs would let an attacker exfiltrate local
    files — must be rejected outright."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="Unsupported webhook protocol"):
            svc._validate_webhook_url("file:///etc/passwd")


def test_gopher_scheme_rejected():
    """gopher:// is the classic SSRF protocol-smuggling vector."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="Unsupported webhook protocol"):
            svc._validate_webhook_url("gopher://example.com:1234/")


# =========================================================================
# _validate_webhook_url — hostname validation
# =========================================================================


def test_no_hostname_rejected():
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="must include a hostname"):
            svc._validate_webhook_url("https:///path-only")


def test_aws_metadata_endpoint_blocked():
    """[SSRF] 169.254.169.254 is the AWS instance-metadata service —
    blocking it prevents IAM credential exfiltration."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="blocked metadata endpoint"):
            svc._validate_webhook_url("https://169.254.169.254/latest/meta-data/")


def test_gcp_metadata_endpoint_blocked():
    """[SSRF] GCP metadata service via DNS name."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="blocked metadata endpoint"):
            svc._validate_webhook_url("https://metadata.google.internal/")


def test_metadata_short_name_blocked():
    """[SSRF] Some k8s clusters resolve the bare ``metadata`` short name
    via search-domain DNS — block it too."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="blocked metadata endpoint"):
            svc._validate_webhook_url("https://metadata/")


# =========================================================================
# _validate_webhook_url — IP-resolution branches (mocked DNS)
# =========================================================================


def _mock_resolves_to(ip: str):
    """Patch socket.getaddrinfo to always return the given IP."""
    return patch.object(
        svc.socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)),
        ],
    )


def test_url_resolving_to_private_ip_rejected():
    """[SSRF] Hostname that resolves to RFC1918 (10.0.0.5) must be
    rejected — internal services must not be reachable via webhook."""
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("10.0.0.5"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://internal-target.example.com/")


def test_url_resolving_to_192_168_rejected():
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("192.168.1.50"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://router.local/")


def test_url_resolving_to_172_16_rejected():
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("172.16.5.5"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://docker-host/")


def test_url_resolving_to_loopback_rejected_in_prod():
    """[SSRF] 127.0.0.1 must be rejected in production — local services
    are never legitimate webhook targets there."""
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("127.0.0.1"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://hooks.example.com/")


def test_url_resolving_to_link_local_rejected():
    """[SSRF] 169.254.0.0/16 includes EC2 metadata + link-local ranges."""
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("169.254.99.99"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://hooks.example.com/")


def test_url_resolving_to_reserved_rejected():
    """[SSRF] 240.0.0.0/4 (Class E) is reserved — block it."""
    with patch.object(svc, "_DEV_MODE", False), _mock_resolves_to("240.0.0.1"):
        with pytest.raises(ValueError, match="internal/private IP"):
            svc._validate_webhook_url("https://hooks.example.com/")


def test_dns_resolution_failure_rejected():
    """A URL whose hostname can't be resolved is also rejected — fail
    closed."""
    with (
        patch.object(svc, "_DEV_MODE", False),
        patch.object(svc.socket, "getaddrinfo", side_effect=socket.gaierror("no such host")),
    ):
        with pytest.raises(ValueError, match="Cannot resolve webhook hostname"):
            svc._validate_webhook_url("https://nonexistent-host-xyz-12345.example/")


def test_dev_mode_localhost_skips_ip_check():
    """In dev mode, the loopback shortcut should NOT call getaddrinfo at
    all — pinning the fast path."""
    with (
        patch.object(svc, "_DEV_MODE", True),
        patch.object(svc.socket, "getaddrinfo") as mock_resolve,
    ):
        out = svc._validate_webhook_url("http://localhost:8080/hook")
        assert out == "http://localhost:8080/hook"
        mock_resolve.assert_not_called()


# =========================================================================
# send_email_with_attachment — SMTP-not-configured fallback
# =========================================================================


async def test_send_email_no_smtp_short_circuits(caplog):
    """Without SMTP config, the function should log and return — never
    attempt a real connection. This is the dev-mode fallback path."""
    import logging

    with patch.object(svc, "Settings", create=True, side_effect=Exception("no settings")):
        # Either branch — Settings raise OR no SMTP_HOST — short-circuits.
        with caplog.at_level(logging.INFO):
            await svc.send_email_with_attachment(
                to_email="user@example.com",
                subject="hi",
                body_html="<p>x</p>",
            )
    # No exception raised — that's the contract.


# =========================================================================
# post_webhook — validation-first
# =========================================================================


async def test_post_webhook_validates_before_sending():
    """[SSRF] post_webhook must run validation BEFORE attempting the
    HTTP call — otherwise a malicious URL would still get hit even if
    the request fails downstream."""
    with patch.object(svc, "_DEV_MODE", False):
        with pytest.raises(ValueError, match="metadata endpoint"):
            await svc.post_webhook(
                "https://169.254.169.254/latest/meta-data/iam/credentials",
                {"event": "x"},
            )
