"""Tests for JWT key rotation functionality."""

from unittest.mock import AsyncMock, patch

import pytest

from app.utils.security import (
    create_access_token,
    decode_access_token,
    decode_refresh_token,
)


class TestJWTKeyRotation:
    """Test JWT key rotation and fallback behavior."""

    def test_token_created_with_current_key_decodes(self):
        token = create_access_token({"sub": "user-1", "org_id": "org-1"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "user-1"

    def test_token_with_wrong_key_returns_none(self):
        import jwt as pyjwt

        token = pyjwt.encode(
            {
                "sub": "user-1",
                "org_id": "org-1",
                "type": "access",
                "jti": "test-jti",
                "iss": "constructai",
                "aud": "constructai-api",
                "exp": 9999999999,
            },
            "completely-wrong-key",
            algorithm="HS256",
        )
        result = decode_access_token(token)
        assert result is None

    @patch("app.utils.security.settings")
    def test_token_with_previous_key_decodes_during_rotation(self, mock_settings):
        """During rotation, tokens signed with the previous key should still decode."""
        import jwt as pyjwt

        old_key = "old-secret-key-that-was-rotated-out-min32chars"
        new_key = "new-secret-key-after-rotation-minimum32chars"

        mock_settings.JWT_SECRET_KEY = new_key
        mock_settings.JWT_SECRET_KEY_PREVIOUS = old_key
        mock_settings.JWT_ALGORITHM = "HS256"

        # Token signed with old key
        token = pyjwt.encode(
            {
                "sub": "user-1",
                "org_id": "org-1",
                "type": "access",
                "jti": "test-jti",
                "iss": "constructai",
                "aud": "constructai-api",
                "exp": 9999999999,
            },
            old_key,
            algorithm="HS256",
        )

        result = decode_access_token(token)
        assert result is not None
        assert result["sub"] == "user-1"

    @patch("app.utils.security.settings")
    def test_refresh_token_with_previous_key_decodes(self, mock_settings):
        import jwt as pyjwt

        old_key = "old-secret-key-that-was-rotated-out-min32chars"
        new_key = "new-secret-key-after-rotation-minimum32chars"

        mock_settings.JWT_SECRET_KEY = new_key
        mock_settings.JWT_SECRET_KEY_PREVIOUS = old_key
        mock_settings.JWT_ALGORITHM = "HS256"

        token = pyjwt.encode(
            {
                "sub": "user-1",
                "org_id": "org-1",
                "type": "refresh",
                "jti": "test-jti",
                "iss": "constructai",
                "aud": "constructai-api",
                "exp": 9999999999,
            },
            old_key,
            algorithm="HS256",
        )

        result = decode_refresh_token(token)
        assert result is not None

    def test_access_token_wrong_type_returns_none(self):
        """A refresh token should not be accepted as an access token."""
        from app.utils.security import create_refresh_token

        token = create_refresh_token({"sub": "user-1", "org_id": "org-1"})
        result = decode_access_token(token)
        assert result is None


class TestRotateJWTKeyFunction:
    """Test the rotate_jwt_key async function."""

    async def test_rotate_stores_keys_in_redis(self):
        from unittest.mock import MagicMock

        mock_pipe = MagicMock()
        mock_pipe.set = MagicMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, 2])

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        async def fake_get_redis():
            return mock_redis

        with patch(
            "app.services.security.redis_state._get_redis",
            side_effect=fake_get_redis,
        ):
            from app.utils.security import rotate_jwt_key

            version = await rotate_jwt_key("new-key-min-32-characters-long-ok")
            assert version == 2
            assert mock_pipe.set.call_count == 2
            mock_pipe.incr.assert_called_once_with("cai:jwt:version")

    async def test_rotate_fails_without_redis(self):
        with patch(
            "app.services.security.redis_state._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.utils.security import rotate_jwt_key

            with pytest.raises(RuntimeError, match="Redis required"):
                await rotate_jwt_key("new-key-min-32-characters-long-ok")
