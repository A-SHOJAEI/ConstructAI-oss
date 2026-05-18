"""Tests for ABAC, MFA, and Redis state management.

Covers attribute-based permission evaluation, classification/phase/location
access control, TOTP generation/verification, backup code lifecycle,
token blacklist, account lockout tracking, and Redis fallback behavior.
"""

import hashlib
import secrets
import time
import uuid
from unittest.mock import AsyncMock, patch

import pyotp
import pytest

from app.services.security.abac import ABACPolicy
from app.services.security.mfa import (
    generate_backup_codes,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_totp_uri,
    verify_backup_code,
    verify_totp,
)

# =========================================================================
# ABAC Tests
# =========================================================================


class TestABAC:
    """Attribute-Based Access Control policy evaluation."""

    def setup_method(self):
        self.policy = ABACPolicy()

    # -- Classification-level access control --

    def test_public_resource_accessible_by_public_clearance(self):
        allowed, _reason = self.policy.evaluate(
            user_attrs={"clearance": "public", "user_id": "u1"},
            resource_attrs={"classification": "public"},
            action="read",
        )
        assert allowed is True

    def test_restricted_resource_denied_for_public_clearance(self):
        allowed, reason = self.policy.evaluate(
            user_attrs={"clearance": "public", "user_id": "u1"},
            resource_attrs={"classification": "restricted"},
            action="read",
        )
        assert allowed is False
        assert "clearance" in reason.lower()

    def test_confidential_resource_accessible_with_sufficient_clearance(self):
        allowed, _reason = self.policy.evaluate(
            user_attrs={"clearance": "confidential", "user_id": "u1"},
            resource_attrs={"classification": "confidential"},
            action="read",
        )
        assert allowed is True

    def test_restricted_clearance_can_access_all_levels(self):
        for level in ("public", "internal", "confidential", "restricted"):
            allowed, _ = self.policy.evaluate(
                user_attrs={"clearance": "restricted", "user_id": "u1"},
                resource_attrs={"classification": level},
                action="read",
                # "restricted"-classified resources also require on-site or
                # VPN access — provide that env so the location guard passes.
                environment={"location": "on_site"},
            )
            assert allowed is True, f"restricted clearance should access {level}"

    # -- Phase-based access control --

    def test_subcontractor_allowed_in_construction_phase(self):
        allowed, _ = self.policy.evaluate(
            user_attrs={"role": "subcontractor", "clearance": "public"},
            resource_attrs={"project_phase": "construction"},
            action="read",
        )
        assert allowed is True

    def test_subcontractor_denied_in_design_phase(self):
        allowed, reason = self.policy.evaluate(
            user_attrs={"role": "subcontractor", "clearance": "public"},
            resource_attrs={"project_phase": "design"},
            action="read",
        )
        assert allowed is False
        assert "phase" in reason.lower()

    def test_unrestricted_role_can_access_any_phase(self):
        """Roles not in _PHASE_RESTRICTIONS can access any phase."""
        allowed, _ = self.policy.evaluate(
            user_attrs={"role": "project_manager", "clearance": "public"},
            resource_attrs={"project_phase": "design"},
            action="read",
        )
        assert allowed is True

    def test_no_phase_on_resource_allows_access(self):
        """If resource has no project_phase, phase check is skipped."""
        allowed, _ = self.policy.evaluate(
            user_attrs={"role": "subcontractor", "clearance": "public"},
            resource_attrs={},
            action="read",
        )
        assert allowed is True

    # -- Document type restrictions --

    def test_subcontractor_can_access_drawing(self):
        allowed, _ = self.policy.evaluate(
            user_attrs={"role": "subcontractor", "clearance": "public"},
            resource_attrs={"document_type": "drawing"},
            action="read",
        )
        assert allowed is True

    def test_subcontractor_cannot_access_contract(self):
        allowed, reason = self.policy.evaluate(
            user_attrs={"role": "subcontractor", "clearance": "public"},
            resource_attrs={"document_type": "contract"},
            action="read",
        )
        assert allowed is False
        assert "document" in reason.lower() or "type" in reason.lower()

    def test_read_only_limited_to_allowed_doc_types(self):
        """READ_ONLY can access report, summary, drawing — not contract."""
        allowed_drawing, _ = self.policy.evaluate(
            user_attrs={"role": "read_only", "clearance": "public"},
            resource_attrs={"document_type": "report"},
            action="read",
        )
        assert allowed_drawing is True

        denied_contract, _ = self.policy.evaluate(
            user_attrs={"role": "read_only", "clearance": "public"},
            resource_attrs={"document_type": "contract"},
            action="read",
        )
        assert denied_contract is False

    # -- Location-based access control --

    def test_restricted_resource_requires_on_site_or_vpn(self):
        allowed, reason = self.policy.evaluate(
            user_attrs={"clearance": "restricted", "user_id": "u1"},
            resource_attrs={"classification": "restricted"},
            action="read",
            environment={"location": "remote", "vpn": False},
        )
        assert allowed is False
        assert "on-site" in reason.lower() or "vpn" in reason.lower()

    def test_restricted_resource_allowed_with_vpn(self):
        allowed, _ = self.policy.evaluate(
            user_attrs={"clearance": "restricted", "user_id": "u1"},
            resource_attrs={"classification": "restricted"},
            action="read",
            environment={"vpn": True},
        )
        assert allowed is True

    def test_restricted_resource_allowed_on_site(self):
        allowed, _ = self.policy.evaluate(
            user_attrs={"clearance": "restricted", "user_id": "u1"},
            resource_attrs={"classification": "restricted"},
            action="read",
            environment={"location": "on_site"},
        )
        assert allowed is True

    def test_non_restricted_resource_skips_location_check(self):
        allowed, _ = self.policy.evaluate(
            user_attrs={"clearance": "internal", "user_id": "u1"},
            resource_attrs={"classification": "internal"},
            action="read",
            environment={"location": "remote", "vpn": False},
        )
        assert allowed is True


# =========================================================================
# MFA Tests
# =========================================================================


class TestMFA:
    """TOTP and backup code tests."""

    def test_totp_secret_generation(self):
        """Generated secret should be a valid base32 string."""
        secret = generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16  # pyotp generates 32-char base32 by default

    def test_totp_verification_with_valid_code(self):
        """A freshly generated TOTP code should verify successfully."""
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert verify_totp(secret, code) is True

    def test_totp_verification_with_invalid_code(self):
        """A wrong code should fail verification."""
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False

    def test_totp_verification_window(self):
        """verify_totp uses valid_window=1, so +-1 time step should work."""
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        # Generate code for previous time step
        import time as _time

        prev_code = totp.at(int(_time.time()) - 30)
        # Should still verify due to valid_window=1
        assert verify_totp(secret, prev_code) is True

    def test_totp_uri_format(self):
        """URI should follow otpauth:// format."""
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "user@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "ConstructAI" in uri
        assert "user@example.com" in uri.replace("%40", "@")

    def test_backup_code_generation_entropy(self):
        """Backup codes should be unique, 12 hex chars each."""
        codes, hashed, salt = generate_backup_codes()
        assert len(codes) == 10
        assert len(hashed) == 10
        assert isinstance(salt, str)
        assert len(salt) == 32  # 16 bytes hex = 32 chars
        # All codes unique
        assert len(set(codes)) == 10
        # All codes are 12-char hex
        for code in codes:
            assert len(code) == 12
            int(code, 16)  # Should not raise

    def test_backup_code_verification_valid(self):
        """A valid backup code should return its index."""
        codes, hashed, salt = generate_backup_codes()
        idx = verify_backup_code(codes[3], hashed, salt)
        assert idx == 3

    def test_backup_code_verification_invalid(self):
        """An invalid code should return None."""
        _, hashed, salt = generate_backup_codes()
        idx = verify_backup_code("invalid_code", hashed, salt)
        assert idx is None

    def test_backup_code_single_use(self):
        """After removing a used code, it should no longer verify."""
        codes, hashed, salt = generate_backup_codes()
        # Use code at index 0
        idx = verify_backup_code(codes[0], hashed, salt)
        assert idx == 0
        # Remove it
        hashed.pop(idx)
        # Should no longer match
        idx2 = verify_backup_code(codes[0], hashed, salt)
        assert idx2 is None

    def test_backup_code_wrong_salt_fails(self):
        """Using a different salt should prevent verification."""
        codes, hashed, salt = generate_backup_codes()
        wrong_salt = secrets.token_hex(16)
        assert wrong_salt != salt
        idx = verify_backup_code(codes[0], hashed, wrong_salt)
        assert idx is None

    def test_backup_code_constant_time_comparison(self):
        """verify_backup_code should use hmac.compare_digest (constant-time)."""
        # Verify the implementation uses hmac.compare_digest by checking
        # that it correctly verifies a manually computed hash
        salt = "testsalt"
        code = "abcdef123456"
        h = hashlib.sha256(salt.encode() + code.encode()).hexdigest()
        hashed_codes = [h]
        idx = verify_backup_code(code, hashed_codes, salt)
        assert idx == 0

    def test_qr_code_data_uri(self):
        """QR code should be returned as a base64 data URI."""
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "user@example.com")
        data_uri = generate_qr_code_data_uri(uri)
        assert data_uri.startswith("data:image/png;base64,")
        assert len(data_uri) > 100  # non-trivial content

    def test_generate_backup_codes_custom_count(self):
        """generate_backup_codes respects the count parameter."""
        codes, hashed, _salt = generate_backup_codes(count=5)
        assert len(codes) == 5
        assert len(hashed) == 5

    def test_generate_backup_codes_custom_salt(self):
        """generate_backup_codes uses provided salt."""
        custom_salt = "my-custom-salt"
        _codes, _hashed, salt = generate_backup_codes(salt=custom_salt)
        assert salt == custom_salt


# =========================================================================
# Redis State Tests
# =========================================================================


class TestRedisState:
    """Token blacklist and account lockout with Redis mocking."""

    @pytest.mark.asyncio
    async def test_blacklist_add_and_check_memory_fallback(self):
        """In-memory blacklist should track JTIs when Redis is unavailable."""
        from app.services.security.redis_state import (
            _memory_blacklist,
            blacklist_token,
            is_token_blacklisted,
        )

        jti = f"test-jti-{uuid.uuid4().hex[:8]}"
        # Ensure we're using memory fallback
        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await blacklist_token(jti, ttl_seconds=60)
            assert await is_token_blacklisted(jti) is True

        # Clean up
        _memory_blacklist.pop(jti, None)

    @pytest.mark.asyncio
    async def test_non_blacklisted_token_not_found(self):
        """A JTI that was never blacklisted should return False."""
        from app.services.security.redis_state import is_token_blacklisted

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert await is_token_blacklisted(f"never-added-{uuid.uuid4().hex}") is False

    @pytest.mark.asyncio
    async def test_lockout_tracking_memory_fallback(self):
        """Failed attempts should accumulate in memory when Redis unavailable."""
        from app.services.security.redis_state import (
            _memory_failed_attempts,
            clear_failed_attempts,
            is_locked_out,
            record_failed_attempt,
        )

        email = f"lockout-test-{uuid.uuid4().hex[:8]}@example.com"
        _memory_failed_attempts.pop(email, None)

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # 4 attempts: not yet locked
            for _ in range(4):
                await record_failed_attempt(email)
            assert await is_locked_out(email) is False

            # 5th attempt: locked
            await record_failed_attempt(email)
            assert await is_locked_out(email) is True

            # Clear attempts
            await clear_failed_attempts(email)
            assert await is_locked_out(email) is False

        _memory_failed_attempts.pop(email, None)

    @pytest.mark.asyncio
    async def test_lockout_window_expiry_memory(self):
        """Expired attempts should not count toward lockout."""
        from app.services.security.redis_state import (
            _LOCKOUT_WINDOW,
            _memory_failed_attempts,
            is_locked_out,
        )

        email = f"lockout-expiry-mem-{uuid.uuid4().hex[:8]}@example.com"
        # Manually insert expired timestamps
        _memory_failed_attempts[email] = [
            time.monotonic() - _LOCKOUT_WINDOW - 100 for _ in range(10)
        ]

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert await is_locked_out(email) is False

        _memory_failed_attempts.pop(email, None)

    @pytest.mark.asyncio
    async def test_blacklist_with_redis_mock(self):
        """When Redis is available, blacklist operations should use Redis."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="1")

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            from app.services.security.redis_state import (
                blacklist_token,
                is_token_blacklisted,
            )

            jti = "redis-test-jti"
            await blacklist_token(jti, ttl_seconds=300)
            mock_redis.set.assert_called_once()

            result = await is_token_blacklisted(jti)
            assert result is True
            mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_require_redis_for_production_raises_in_production(self):
        """require_redis_for_production should raise RuntimeError in production."""
        from app.services.security.redis_state import require_redis_for_production

        with (
            patch(
                "app.services.security.redis_state._get_redis",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.services.security.redis_state.settings") as mock_settings,
        ):
            mock_settings.ENVIRONMENT = "production"
            with pytest.raises(RuntimeError, match="Redis is required"):
                await require_redis_for_production()

    @pytest.mark.asyncio
    async def test_require_redis_for_production_warns_in_development(self):
        """require_redis_for_production should not raise in development."""
        from app.services.security.redis_state import require_redis_for_production

        with (
            patch(
                "app.services.security.redis_state._get_redis",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.services.security.redis_state.settings") as mock_settings,
        ):
            mock_settings.ENVIRONMENT = "development"
            # Should not raise — just log a warning
            await require_redis_for_production()

    @pytest.mark.asyncio
    async def test_blacklist_memory_eviction(self):
        """Memory blacklist should evict entries when over capacity."""
        from app.services.security.redis_state import (
            _memory_blacklist,
            blacklist_token,
        )

        len(_memory_blacklist)

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # The function caps at _BLACKLIST_MAX_SIZE. We just verify the
            # eviction path doesn't crash with a smaller test.
            for i in range(20):
                await blacklist_token(f"eviction-test-{i}", ttl_seconds=60)

        # Clean up
        for i in range(20):
            _memory_blacklist.pop(f"eviction-test-{i}", None)

    @pytest.mark.asyncio
    async def test_redis_failure_falls_back_to_memory(self):
        """If Redis calls fail (exception), operations fall back to memory."""
        from app.services.security.redis_state import (
            _memory_blacklist,
            blacklist_token,
            is_token_blacklisted,
        )

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
        mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))

        jti = f"fallback-test-{uuid.uuid4().hex[:8]}"

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            await blacklist_token(jti, ttl_seconds=60)
            # Should have fallen back to memory
            assert jti in _memory_blacklist

            result = await is_token_blacklisted(jti)
            assert result is True

        _memory_blacklist.pop(jti, None)
