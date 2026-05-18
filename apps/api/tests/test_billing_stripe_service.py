"""Tests for the billing / Stripe service stubs.

These functions wrap a thin DB query with permissive defaults — no real
Stripe traffic. The product-gating logic is what controls feature access
across the platform, so the tests pin every branch.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing.stripe_service import (
    _DEFAULT_PRODUCTS,
    get_enabled_products,
    get_subscription,
    is_product_enabled,
    record_usage,
)


def _db_returning(record):
    """Build an AsyncMock session whose .execute(...).scalars().first()
    returns ``record``."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = record
    db.execute = AsyncMock(return_value=result)
    return db


# ---- get_subscription ---------------------------------------------------


async def test_get_subscription_returns_record_when_present():
    sub = MagicMock()
    db = _db_returning(sub)
    out = await get_subscription(db, uuid.uuid4())
    assert out is sub


async def test_get_subscription_returns_none_when_absent():
    db = _db_returning(None)
    out = await get_subscription(db, uuid.uuid4())
    assert out is None


# ---- get_enabled_products ----------------------------------------------


async def test_get_enabled_products_returns_default_for_free_tier():
    """Without a subscription record, the helper falls back to the full
    default list so a fresh org isn't blocked from any feature in dev /
    free tier."""
    db = _db_returning(None)
    products = await get_enabled_products(db, uuid.uuid4())
    assert products == list(_DEFAULT_PRODUCTS)
    # Defensive: returning a fresh list each time so callers can mutate
    # freely without nuking the canonical set.
    products.append("never-product")
    assert "never-product" not in _DEFAULT_PRODUCTS


async def test_get_enabled_products_uses_subscription_when_present():
    sub = MagicMock()
    sub.products_enabled = ["sitescribe", "rfi_copilot"]
    db = _db_returning(sub)
    products = await get_enabled_products(db, uuid.uuid4())
    assert products == ["sitescribe", "rfi_copilot"]


async def test_get_enabled_products_with_empty_subscription():
    """A subscription that explicitly enables zero products must hide
    every feature — the empty list isn't a "missing config" signal."""
    sub = MagicMock()
    sub.products_enabled = []
    db = _db_returning(sub)
    products = await get_enabled_products(db, uuid.uuid4())
    assert products == []


# ---- is_product_enabled -------------------------------------------------


async def test_is_product_enabled_yes_for_default_tier():
    db = _db_returning(None)
    for product in _DEFAULT_PRODUCTS:
        assert await is_product_enabled(db, uuid.uuid4(), product) is True


async def test_is_product_enabled_no_for_unknown_product_in_default_tier():
    db = _db_returning(None)
    assert await is_product_enabled(db, uuid.uuid4(), "ghost_product") is False


async def test_is_product_enabled_with_explicit_subscription():
    sub = MagicMock()
    sub.products_enabled = ["sitescribe"]
    db = _db_returning(sub)
    assert await is_product_enabled(db, uuid.uuid4(), "sitescribe") is True
    assert await is_product_enabled(db, uuid.uuid4(), "carbonlens") is False


# ---- record_usage --------------------------------------------------------


async def test_record_usage_writes_event_to_session():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    org = uuid.uuid4()
    project = uuid.uuid4()

    await record_usage(
        db,
        org_id=org,
        project_id=project,
        product="sitescribe",
        event_type="photo_uploaded",
        quantity=3,
        metadata={"source": "ios_app"},
    )

    assert db.add.call_count == 1
    event = db.add.call_args[0][0]
    assert event.organization_id == org
    assert event.project_id == project
    assert event.product == "sitescribe"
    assert event.event_type == "photo_uploaded"
    assert event.quantity == 3
    assert event.metadata_ == {"source": "ios_app"}
    db.flush.assert_awaited_once()


async def test_record_usage_defaults_metadata_to_empty_dict():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    await record_usage(
        db,
        org_id=uuid.uuid4(),
        project_id=None,
        product="rfi_copilot",
        event_type="rfi_drafted",
    )
    event = db.add.call_args[0][0]
    assert event.project_id is None
    assert event.quantity == 1  # default
    assert event.metadata_ == {}


# ---- _DEFAULT_PRODUCTS sanity check ------------------------------------


@pytest.mark.parametrize(
    "expected",
    ["sitescribe", "rfi_copilot", "closeout_iq", "heatshield", "wageguard", "carbonlens"],
)
def test_default_products_contains_expected(expected):
    """Pin the documented free-tier product set so a refactor can't
    accidentally drop one and silently downgrade every existing org."""
    assert expected in _DEFAULT_PRODUCTS
