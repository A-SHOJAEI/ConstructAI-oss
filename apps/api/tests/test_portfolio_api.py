"""Tests for Portfolio dashboard API endpoints.

The /api/v1/portfolio/* endpoints are 501 stubs pending the production
portfolio aggregation pipeline. We assert the 501 contract until the
endpoints exist.
"""

from __future__ import annotations

from tests.conftest import *  # noqa: F403


class TestPortfolioAPI:
    async def test_get_portfolio(self, client, auth_headers):
        response = await client.get(
            "/api/v1/portfolio",
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_get_benchmarks(self, client, auth_headers):
        response = await client.get(
            "/api/v1/portfolio/benchmarks",
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_get_portfolio_map(
        self,
        client,
        auth_headers,
    ):
        response = await client.get(
            "/api/v1/portfolio/map",
            headers=auth_headers,
        )
        assert response.status_code == 501
