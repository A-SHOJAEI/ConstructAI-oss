"""Mock response data for Phase 1 tests.

All external API responses (Voyage AI, Cohere, OpenAI/LLM) are mocked here
so that tests never make real network calls.
"""

import json

# ---------------------------------------------------------------------------
# Voyage AI embedding mock
# ---------------------------------------------------------------------------

MOCK_VOYAGE_EMBEDDING: list[float] = [0.1] * 1024

# ---------------------------------------------------------------------------
# Cohere rerank mock
# ---------------------------------------------------------------------------


class _MockRerankResult:
    """Minimal stand-in for a single Cohere rerank result item."""

    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


class MockCohereRerankResponse:
    """Minimal stand-in for ``cohere.types.RerankResponse``."""

    def __init__(self, results: list[_MockRerankResult] | None = None):
        if results is None:
            results = [
                _MockRerankResult(index=2, relevance_score=0.95),
                _MockRerankResult(index=0, relevance_score=0.80),
                _MockRerankResult(index=1, relevance_score=0.55),
            ]
        self.results = results


MOCK_COHERE_RERANK_RESPONSE = MockCohereRerankResponse()

# ---------------------------------------------------------------------------
# LLM classification mock
# ---------------------------------------------------------------------------

MOCK_LLM_CLASSIFICATION_RESPONSE: str = json.dumps(
    {
        "classified_type": "specification",
        "csi_division": "03 - Concrete",
        "discipline": "structural",
        "confidence": 0.92,
    }
)

# ---------------------------------------------------------------------------
# LLM entity extraction mock
# ---------------------------------------------------------------------------

MOCK_LLM_ENTITY_RESPONSE: str = json.dumps(
    {
        "entities": [
            {
                "entity_type": "product",
                "entity_value": "Type I/II Portland Cement",
                "section_reference": "03 30 00 - 2.1",
                "confidence": 0.95,
            },
            {
                "entity_type": "standard",
                "entity_value": "ASTM C150",
                "section_reference": "03 30 00 - 2.1",
                "confidence": 0.97,
            },
            {
                "entity_type": "requirement",
                "entity_value": "Minimum compressive strength 4000 psi at 28 days",
                "section_reference": "03 30 00 - 3.2",
                "confidence": 0.90,
            },
            {
                "entity_type": "manufacturer",
                "entity_value": "LafargeHolcim",
                "section_reference": "03 30 00 - 2.1",
                "confidence": 0.88,
            },
            {
                "entity_type": "test_required",
                "entity_value": "Slump test per ASTM C143",
                "section_reference": "03 30 00 - 3.3",
                "confidence": 0.93,
            },
        ]
    }
)

# ---------------------------------------------------------------------------
# LLM RAG answer mock
# ---------------------------------------------------------------------------

MOCK_LLM_ANSWER_RESPONSE: str = json.dumps(
    {
        "answer": (
            "According to the project specifications, the concrete mix design "
            "shall achieve a minimum compressive strength of 4,000 psi at 28 days "
            "[Test Construction Specification, p. 1]. The cement shall conform to "
            "ASTM C150, Type I/II Portland Cement [Test Construction Specification, p. 1]."
        ),
        "confidence": 0.88,
        "sources": [
            {
                "document_title": "Test Construction Specification",
                "page_number": 1,
                "section": "03 30 00",
            }
        ],
    }
)

# ---------------------------------------------------------------------------
# Sample PDF text
# ---------------------------------------------------------------------------

MOCK_PDF_TEXT: str = """\
SECTION 03 30 00 - CAST-IN-PLACE CONCRETE

PART 1 - GENERAL

1.1 SUMMARY
This section includes cast-in-place concrete for the following:
A. Foundations and footings
B. Slabs on grade
C. Elevated structural slabs
D. Concrete walls and columns

1.2 REFERENCES
A. ACI 301 - Specifications for Structural Concrete
B. ACI 318 - Building Code Requirements for Structural Concrete
C. ASTM C150 - Standard Specification for Portland Cement
D. ASTM C143 - Standard Test Method for Slump of Hydraulic-Cement Concrete

PART 2 - PRODUCTS

2.1 MATERIALS
A. Cement: Portland cement conforming to ASTM C150, Type I/II.
   Manufacturer: LafargeHolcim or approved equal.
B. Aggregates: Conforming to ASTM C33.
C. Water: Clean, potable, free of oils and organic matter.
D. Admixtures: Air-entraining per ASTM C260.

2.2 CONCRETE MIX DESIGN
A. Normal-weight concrete: 4,000 psi minimum compressive strength at 28 days.
B. Maximum water-cement ratio: 0.45
C. Air content: 5 percent plus or minus 1.5 percent.
D. Maximum slump: 4 inches (unless otherwise indicated).

PART 3 - EXECUTION

3.1 FORMWORK
A. Design, erect, and maintain formwork to support all loads.
B. Maintain tolerances per ACI 117.

3.2 PLACING CONCRETE
A. Place concrete within 90 minutes of batching.
B. Do not place concrete when ambient temperature is below 40 degrees F.
C. Consolidate with internal vibrators.

3.3 TESTING
A. Perform slump tests per ASTM C143 for each load.
B. Cast and cure test cylinders per ASTM C31.
C. Test cylinders at 7 and 28 days per ASTM C39.
"""
