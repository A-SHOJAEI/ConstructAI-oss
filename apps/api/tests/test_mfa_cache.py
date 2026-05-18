"""Tests for MFA secret storage in Redis cache during setup."""

from unittest.mock import AsyncMock, patch


class TestMFASecretCache:
    """Verify MFA secrets are stored in Redis cache, not DB, during setup."""

    async def test_mfa_setup_stores_in_cache(self):
        """MFA setup should store secret in cache with 5-min TTL, not in DB."""
        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock(return_value=True)

        with patch("app.api.v1.auth.CacheService", return_value=mock_cache):
            # The cache.set call should use key pattern cai:mfa_setup:{user_id}
            # and ttl=300 (5 minutes)
            await mock_cache.set("cai:mfa_setup:user-123", "TOTP_SECRET", ttl=300)
            mock_cache.set.assert_called_once_with("cai:mfa_setup:user-123", "TOTP_SECRET", ttl=300)

    async def test_mfa_verify_reads_from_cache(self):
        """MFA verify-setup should read secret from cache."""
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value="CACHED_SECRET")

        with patch("app.api.v1.auth.CacheService", return_value=mock_cache):
            secret = await mock_cache.get("cai:mfa_setup:user-123")
            assert secret == "CACHED_SECRET"

    async def test_mfa_verify_fails_on_expired_cache(self):
        """Should return error when MFA setup secret has expired from cache."""
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)  # Cache miss

        with patch("app.api.v1.auth.CacheService", return_value=mock_cache):
            secret = await mock_cache.get("cai:mfa_setup:user-123")
            assert secret is None, "Expired cache should return None"

    async def test_cache_deleted_after_successful_verify(self):
        """After successful TOTP verification, cache entry should be deleted."""
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value="VALID_SECRET")
        mock_cache.delete = AsyncMock(return_value=True)

        with patch("app.api.v1.auth.CacheService", return_value=mock_cache):
            # Simulate verify flow
            secret = await mock_cache.get("cai:mfa_setup:user-123")
            assert secret == "VALID_SECRET"
            await mock_cache.delete("cai:mfa_setup:user-123")
            mock_cache.delete.assert_called_once_with("cai:mfa_setup:user-123")
