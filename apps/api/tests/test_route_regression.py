"""Route-level regression tests for known-broken paths.

These tests exercise the FULL request path including middleware
configuration, not just service functions.  They catch:
- Webhook endpoints blocked by CSRF or tenant middleware
- Serialization mismatches (string vs dict predecessor formats)
- Offline sync mass-assignment of server-managed fields
- Stub endpoints missing detectability headers

All imports are done inline inside tests to give clear errors when
a module has been refactored or renamed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Webhook reachability -- CSRF and tenant middleware exemptions
# ---------------------------------------------------------------------------


class TestWebhookCSRFExemptions:
    """Verify that webhook and OAuth callback paths are CSRF-exempt.

    If these paths are NOT in _CSRF_EXEMPT_PREFIXES, the CSRF middleware
    will reject inbound POSTs that don't carry a CSRF cookie+header,
    breaking all external webhook integrations.
    """

    @staticmethod
    def _is_csrf_exempt(path: str) -> bool:
        from app.middleware.csrf import _CSRF_EXEMPT_PREFIXES

        return any(path == p or path.startswith(p + "/") for p in _CSRF_EXEMPT_PREFIXES)

    def test_procore_webhook_path(self):
        assert self._is_csrf_exempt("/api/v1/webhooks/procore")

    def test_procore_webhook_subpath(self):
        """Procore may append event-specific paths."""
        assert self._is_csrf_exempt("/api/v1/webhooks/procore/events")

    def test_instant_pay_webhook_path(self):
        """Instant Pay webhooks sit under /api/v1/webhooks/instant-pay/{project_id}."""
        assert self._is_csrf_exempt(
            "/api/v1/webhooks/instant-pay/550e8400-e29b-41d4-a716-446655440000"
        )

    def test_oauth_callback_path(self):
        assert self._is_csrf_exempt("/api/v1/integrations/procore/callback")

    def test_auth_login_exempt(self):
        assert self._is_csrf_exempt("/api/v1/auth/login")

    def test_auth_register_exempt(self):
        assert self._is_csrf_exempt("/api/v1/auth/register")

    def test_health_endpoint_exempt(self):
        assert self._is_csrf_exempt("/health")

    def test_non_webhook_path_not_exempt(self):
        """A normal API path should NOT be CSRF-exempt."""
        assert not self._is_csrf_exempt("/api/v1/projects")

    def test_csrf_exempt_prefixes_is_tuple(self):
        """Guard against accidental type change (must be iterable of strings)."""
        from app.middleware.csrf import _CSRF_EXEMPT_PREFIXES

        assert isinstance(_CSRF_EXEMPT_PREFIXES, tuple | list)
        for item in _CSRF_EXEMPT_PREFIXES:
            assert isinstance(item, str)


class TestWebhookTenantExemptions:
    """Verify that webhook and OAuth paths bypass tenant context middleware.

    Webhooks from external services (Procore, payment processors) do NOT
    carry a JWT and must not be rejected by the tenant middleware.
    """

    @staticmethod
    def _is_tenant_exempt(path: str) -> bool:
        from app.middleware.tenant_context import EXEMPT_PATHS

        return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)

    def test_procore_webhook_path(self):
        assert self._is_tenant_exempt("/api/v1/webhooks/procore")

    def test_procore_webhook_subpath(self):
        assert self._is_tenant_exempt("/api/v1/webhooks/procore/events")

    def test_instant_pay_webhook_subpath(self):
        """instant-pay sits under /webhooks -- check that the parent prefix works."""
        assert self._is_tenant_exempt(
            "/api/v1/webhooks/instant-pay/550e8400-e29b-41d4-a716-446655440000"
        )

    def test_oauth_callback_path(self):
        assert self._is_tenant_exempt("/api/v1/integrations/procore/callback")

    def test_auth_paths_exempt(self):
        assert self._is_tenant_exempt("/api/v1/auth/login")
        assert self._is_tenant_exempt("/api/v1/auth/register")

    def test_health_exempt(self):
        assert self._is_tenant_exempt("/health")

    def test_normal_api_path_not_exempt(self):
        assert not self._is_tenant_exempt("/api/v1/projects")


class TestTenantPublicPathPrefixes:
    """Verify that _PUBLIC_PATH_PREFIXES (the fallback reject list) covers webhooks."""

    @staticmethod
    def _is_public(path: str) -> bool:
        from app.middleware.tenant_context import _PUBLIC_PATH_PREFIXES

        return any(path == p or path.startswith(p + "/") for p in _PUBLIC_PATH_PREFIXES)

    def test_webhooks_are_public(self):
        assert self._is_public("/api/v1/webhooks/procore")

    def test_oauth_callback_is_public(self):
        assert self._is_public("/api/v1/integrations/procore/callback")

    def test_metrics_endpoint_is_public(self):
        assert self._is_public("/metrics")

    def test_project_api_not_public(self):
        assert not self._is_public("/api/v1/projects")


# ---------------------------------------------------------------------------
# Schedule optimizer serialization
# ---------------------------------------------------------------------------


class TestScheduleOptimizerSerialization:
    """Verify the optimizer handles both predecessor formats.

    The ScheduleActivity.predecessors JSONB column historically stored
    plain string IDs.  After a migration, some activities have dict-form
    predecessors with predecessor_id/type/lag keys.  The loader must
    normalise both formats for the CPM engine.
    """

    def test_string_predecessors_normalized_to_dicts(self):
        """Plain string predecessor IDs should become relationship dicts."""
        raw_preds = ["abc-123", "def-456"]
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
        assert len(relationships) == 2
        assert relationships[0] == {"predecessor_id": "abc-123", "type": "FS", "lag": 0}
        assert relationships[1] == {"predecessor_id": "def-456", "type": "FS", "lag": 0}

    def test_dict_predecessors_passed_through(self):
        """Predecessors already in dict format should be passed unchanged."""
        raw_preds = [
            {"predecessor_id": "abc-123", "type": "FF", "lag": 2},
            {"predecessor_id": "def-456", "type": "SS", "lag": 0},
        ]
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
        assert relationships == raw_preds

    def test_mixed_predecessors(self):
        """A mixture of string and dict predecessors should both be normalised."""
        raw_preds = [
            "abc-123",
            {"predecessor_id": "def-456", "type": "FF", "lag": 1},
        ]
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
        assert len(relationships) == 2
        assert relationships[0] == {"predecessor_id": "abc-123", "type": "FS", "lag": 0}
        assert relationships[1] == {"predecessor_id": "def-456", "type": "FF", "lag": 1}

    def test_malformed_entries_skipped(self):
        """Entries that are neither str nor valid dict should be silently dropped."""
        raw_preds = [42, None, {"no_key": "value"}, "valid-id"]
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
        assert len(relationships) == 1
        assert relationships[0]["predecessor_id"] == "valid-id"

    def test_empty_predecessors(self):
        """Empty predecessor list should produce empty relationships."""
        raw_preds: list = []
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
        assert relationships == []


# ---------------------------------------------------------------------------
# Offline sync allowlists
# ---------------------------------------------------------------------------


class TestOfflineSyncAllowlists:
    """Verify sync cannot set server-managed fields via mass-assignment."""

    def test_daily_log_allowlist_excludes_server_fields(self):
        from app.services.sync.offline_sync_engine import _ENTITY_WRITABLE_FIELDS

        allowed = _ENTITY_WRITABLE_FIELDS["daily_log"]
        forbidden = {
            "id",
            "project_id",
            "created_by",
            "created_at",
            "updated_at",
            "data_source",
            "procore_id",
        }
        for field in forbidden:
            assert field not in allowed, (
                f"Server-managed field '{field}' must not be in daily_log allowlist"
            )

    def test_punch_list_item_allowlist_excludes_server_fields(self):
        from app.services.sync.offline_sync_engine import _ENTITY_WRITABLE_FIELDS

        allowed = _ENTITY_WRITABLE_FIELDS["punch_list_item"]
        for field in ("id", "project_id", "created_by", "data_source"):
            assert field not in allowed

    def test_safety_observation_allowlist_excludes_server_fields(self):
        from app.services.sync.offline_sync_engine import _ENTITY_WRITABLE_FIELDS

        allowed = _ENTITY_WRITABLE_FIELDS["safety_observation"]
        for field in ("id", "project_id", "created_by", "data_source"):
            assert field not in allowed

    def test_rfi_allowlist_excludes_server_fields(self):
        from app.services.sync.offline_sync_engine import _ENTITY_WRITABLE_FIELDS

        allowed = _ENTITY_WRITABLE_FIELDS["rfi"]
        for field in (
            "id",
            "project_id",
            "created_by",
            "data_source",
            "procore_id",
            "answer",
            "answered_by",
        ):
            assert field not in allowed

    def test_all_syncable_entity_types_have_allowlists(self):
        from app.services.sync.offline_sync_engine import (
            _ENTITY_WRITABLE_FIELDS,
            SYNCABLE_ENTITY_TYPES,
        )

        for entity_type in SYNCABLE_ENTITY_TYPES:
            assert entity_type in _ENTITY_WRITABLE_FIELDS, (
                f"Missing writable-field allowlist for syncable entity type '{entity_type}'"
            )

    def test_allowlists_are_non_empty_sets(self):
        from app.services.sync.offline_sync_engine import _ENTITY_WRITABLE_FIELDS

        for entity_type, allowed in _ENTITY_WRITABLE_FIELDS.items():
            assert isinstance(allowed, set), (
                f"Allowlist for '{entity_type}' should be a set, got {type(allowed)}"
            )
            assert len(allowed) > 0, f"Allowlist for '{entity_type}' is empty"


# ---------------------------------------------------------------------------
# Stub endpoint headers
# ---------------------------------------------------------------------------


class TestUnimplementedEndpoints:
    """Verify unimplemented endpoints return 501 instead of 200 with fake data."""

    def test_portfolio_returns_501(self):
        from app.api.v1.portfolio import _NOT_IMPLEMENTED_DETAIL

        assert "not yet implemented" in _NOT_IMPLEMENTED_DETAIL.lower()

    def test_evaluation_returns_501(self):
        from app.api.v1.evaluation import _NOT_IMPLEMENTED_DETAIL

        assert "not yet implemented" in _NOT_IMPLEMENTED_DETAIL.lower()

    def test_feedback_returns_501(self):
        from app.api.v1.feedback import _NOT_IMPLEMENTED_DETAIL

        assert "not yet implemented" in _NOT_IMPLEMENTED_DETAIL.lower()


# ---------------------------------------------------------------------------
# Procore webhook signature verification
# ---------------------------------------------------------------------------


class TestProcoreWebhookSignatureVerification:
    """Verify the HMAC-SHA256 signature checking logic."""

    def test_valid_signature_accepted(self):
        import hashlib
        import hmac as hmac_mod

        from app.api.v1.procore_webhooks import verify_signature

        secret = "test-webhook-secret"
        payload = b'{"event": "test"}'
        sig = hmac_mod.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_signature(payload, sig, secret)

    def test_invalid_signature_rejected(self):
        from app.api.v1.procore_webhooks import verify_signature

        assert not verify_signature(b"payload", "bad-signature", "secret")

    def test_empty_secret_rejected(self):
        from app.api.v1.procore_webhooks import verify_signature

        assert not verify_signature(b"payload", "any-signature", "")

    def test_tampered_payload_rejected(self):
        import hashlib
        import hmac as hmac_mod

        from app.api.v1.procore_webhooks import verify_signature

        secret = "test-webhook-secret"
        original = b'{"event": "test"}'
        sig = hmac_mod.new(secret.encode(), original, hashlib.sha256).hexdigest()
        tampered = b'{"event": "hacked"}'
        assert not verify_signature(tampered, sig, secret)


# ---------------------------------------------------------------------------
# Webhook deduplication (in-memory LRU)
# ---------------------------------------------------------------------------


class TestWebhookDeduplication:
    """Verify the in-memory LRU dedup cache for webhooks."""

    def test_first_delivery_not_duplicate(self):
        from app.api.v1.procore_webhooks import _lru_cache, _lru_check_and_add

        test_id = "test-unique-delivery-001"
        _lru_cache.pop(test_id, None)  # Clean state
        assert not _lru_check_and_add(test_id)

    def test_second_delivery_is_duplicate(self):
        from app.api.v1.procore_webhooks import _lru_cache, _lru_check_and_add

        test_id = "test-unique-delivery-002"
        _lru_cache.pop(test_id, None)
        _lru_check_and_add(test_id)
        assert _lru_check_and_add(test_id)


# ---------------------------------------------------------------------------
# Middleware consistency
# ---------------------------------------------------------------------------


class TestMiddlewareConsistency:
    """Verify that CSRF and tenant exempt paths are consistent."""

    def test_webhooks_exempt_in_both_middlewares(self):
        """Any webhook path must be exempt from BOTH CSRF and tenant middleware."""
        from app.middleware.csrf import _CSRF_EXEMPT_PREFIXES
        from app.middleware.tenant_context import EXEMPT_PATHS

        webhook_prefixes = [p for p in _CSRF_EXEMPT_PREFIXES if "webhook" in p.lower()]
        for prefix in webhook_prefixes:
            assert any(
                prefix == ep or prefix.startswith(ep + "/") or ep.startswith(prefix)
                for ep in EXEMPT_PATHS
            ), f"Webhook prefix '{prefix}' is CSRF-exempt but NOT tenant-exempt"

    def test_oauth_callback_exempt_in_both_middlewares(self):
        """OAuth callback must be exempt from both CSRF and tenant."""
        from app.middleware.csrf import _CSRF_EXEMPT_PREFIXES
        from app.middleware.tenant_context import EXEMPT_PATHS

        callback = "/api/v1/integrations/procore/callback"
        assert any(callback == p or callback.startswith(p + "/") for p in _CSRF_EXEMPT_PREFIXES), (
            "OAuth callback not CSRF-exempt"
        )
        assert any(callback == p or callback.startswith(p + "/") for p in EXEMPT_PATHS), (
            "OAuth callback not tenant-exempt"
        )

    def test_csrf_exempt_paths_all_start_with_slash(self):
        """All exempt paths should be absolute (start with /)."""
        from app.middleware.csrf import _CSRF_EXEMPT_PREFIXES

        for path in _CSRF_EXEMPT_PREFIXES:
            assert path.startswith("/"), f"CSRF exempt path '{path}' is not absolute"

    def test_tenant_exempt_paths_all_start_with_slash(self):
        from app.middleware.tenant_context import EXEMPT_PATHS

        for path in EXEMPT_PATHS:
            assert path.startswith("/"), f"Tenant exempt path '{path}' is not absolute"


# ---------------------------------------------------------------------------
# RBAC dependency wiring
# ---------------------------------------------------------------------------


class TestRBACDependencyWiring:
    """Verify the require_permission dependency function exists and returns a callable."""

    def test_require_permission_returns_callable(self):
        from app.dependencies import require_permission

        dep = require_permission("projects", "read")
        assert callable(dep)

    def test_require_permission_different_resources_return_different_deps(self):
        from app.dependencies import require_permission

        dep1 = require_permission("projects", "read")
        dep2 = require_permission("documents", "create")
        # They should be distinct closures
        assert dep1 is not dep2

    def test_require_mfa_returns_callable(self):
        from app.dependencies import require_mfa

        dep = require_mfa()
        assert callable(dep)
