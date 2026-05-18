"""Tests for the LangGraph checkpointer factory.

Pin the TESTING -> MemorySaver path, the PostgresSaver fallback
to MemorySaver on connection failure, and the import-time error
fallback (langgraph-checkpoint-postgres not installed).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from app.services.agents.checkpointer import get_checkpointer

# =========================================================================
# TESTING mode -> MemorySaver
# =========================================================================


def test_testing_mode_returns_memory_saver():
    """[contract] When settings.TESTING=True, return MemorySaver
    (no DB connection needed in tests). Pin so a refactor can't
    accidentally hit the real DB during unit tests."""
    fake_settings = MagicMock()
    fake_settings.TESTING = True

    with patch("app.config.Settings", return_value=fake_settings):
        cp = get_checkpointer()

    assert isinstance(cp, MemorySaver)


# =========================================================================
# Production mode — PostgresSaver path
# =========================================================================


def test_production_mode_uses_postgres_saver():
    """When TESTING=False, attempt PostgresSaver with DATABASE_URL_SYNC."""
    fake_settings = MagicMock()
    fake_settings.TESTING = False
    fake_settings.DATABASE_URL_SYNC = "postgresql://localhost:5432/test"

    fake_pg_saver = MagicMock()
    fake_pg_saver_instance = MagicMock()
    fake_pg_saver.from_conn_string = MagicMock(return_value=fake_pg_saver_instance)

    fake_module = MagicMock()
    fake_module.PostgresSaver = fake_pg_saver

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch.dict(
            "sys.modules",
            {
                "langgraph.checkpoint.postgres": fake_module,
            },
        ),
    ):
        cp = get_checkpointer()

    fake_pg_saver.from_conn_string.assert_called_once_with("postgresql://localhost:5432/test")
    fake_pg_saver_instance.setup.assert_called_once()
    assert cp is fake_pg_saver_instance


def test_production_postgres_failure_falls_back_to_memory():
    """[fallback] PostgresSaver setup raises (e.g., DB unreachable) ->
    falls back to MemorySaver with warning. Pin so app startup
    doesn't crash on a transient DB issue."""
    fake_settings = MagicMock()
    fake_settings.TESTING = False
    fake_settings.DATABASE_URL_SYNC = "postgresql://invalid/url"

    fake_pg_saver = MagicMock()
    fake_pg_saver.from_conn_string.side_effect = ConnectionError("DB down")

    fake_module = MagicMock()
    fake_module.PostgresSaver = fake_pg_saver

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch.dict("sys.modules", {"langgraph.checkpoint.postgres": fake_module}),
    ):
        cp = get_checkpointer()

    # Falls back to MemorySaver:
    assert isinstance(cp, MemorySaver)


def test_production_setup_failure_falls_back_to_memory():
    """[fallback] PostgresSaver.setup() raises -> fall back."""
    fake_settings = MagicMock()
    fake_settings.TESTING = False
    fake_settings.DATABASE_URL_SYNC = "postgresql://localhost/db"

    fake_pg_saver = MagicMock()
    fake_pg_instance = MagicMock()
    fake_pg_instance.setup.side_effect = RuntimeError("setup migration failed")
    fake_pg_saver.from_conn_string.return_value = fake_pg_instance

    fake_module = MagicMock()
    fake_module.PostgresSaver = fake_pg_saver

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch.dict("sys.modules", {"langgraph.checkpoint.postgres": fake_module}),
    ):
        cp = get_checkpointer()

    assert isinstance(cp, MemorySaver)


def test_production_import_error_falls_back_to_memory():
    """[fallback] langgraph-checkpoint-postgres not installed (or
    sub-import failure) -> fall back to MemorySaver. Pin so the
    optional dependency isn't a hard requirement for app startup."""
    fake_settings = MagicMock()
    fake_settings.TESTING = False
    fake_settings.DATABASE_URL_SYNC = "postgresql://localhost/db"

    # Make the import itself fail by removing the module from sys.modules:
    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch.dict(
            "sys.modules",
            {"langgraph.checkpoint.postgres": None},
        ),
    ):
        cp = get_checkpointer()

    assert isinstance(cp, MemorySaver)
