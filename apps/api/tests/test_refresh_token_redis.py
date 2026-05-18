"""Tests for refresh token JTI tracking — Redis-only, fail closed."""

from unittest.mock import AsyncMock, patch


class TestRefreshJTIRedisOnly:
    """Verify refresh token JTI tracking uses Redis exclusively."""

    async def test_rejects_when_redis_unavailable(self):
        """When Redis is unavailable, should fail closed (reject refresh)."""
        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.services.auth import _check_and_mark_refresh_jti

            result = await _check_and_mark_refresh_jti("test-jti-123")
            assert result is True, "Should return True (used) when Redis unavailable"

    async def test_tracks_via_redis_set_nx(self):
        """Should use Redis SET NX to atomically check and mark JTI."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)  # NX succeeded (new key)

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            from app.services.auth import _check_and_mark_refresh_jti

            result = await _check_and_mark_refresh_jti("fresh-jti")
            assert result is False, "Should return False (not used) for new JTI"
            mock_redis.set.assert_called_once()

    async def test_detects_replay(self):
        """Should detect replay when Redis SET NX returns False."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=False)  # NX failed (key exists)

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            from app.services.auth import _check_and_mark_refresh_jti

            result = await _check_and_mark_refresh_jti("replayed-jti")
            assert result is True, "Should return True (used) for replayed JTI"

    async def test_rejects_on_redis_error(self):
        """Should fail closed on Redis errors."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            from app.services.auth import _check_and_mark_refresh_jti

            result = await _check_and_mark_refresh_jti("error-jti")
            assert result is True, "Should return True (reject) on Redis error"

    def test_no_in_memory_fallback_exists(self):
        """Verify the in-memory dict fallback has been removed."""
        import app.services.auth as auth_module

        assert not hasattr(auth_module, "_used_refresh_jtis"), (
            "_used_refresh_jtis in-memory dict should be removed"
        )
