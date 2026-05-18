"""Sample BIM/IFC data for testing."""

from __future__ import annotations

# Re-export from precon_mock_responses for convenience
from tests.fixtures.precon_mock_responses import MOCK_IFC_DATA


def create_sample_ifc_elements(count: int = 6) -> dict:
    """Create sample IFC element data for testing."""
    return MOCK_IFC_DATA
