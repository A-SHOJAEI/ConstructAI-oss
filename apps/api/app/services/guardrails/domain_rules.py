"""Stage 3: Domain-specific rule validators."""

from __future__ import annotations

import logging
import re
from typing import ClassVar

logger = logging.getLogger(__name__)


class CSIMasterFormatValidator:
    """Validate CSI MasterFormat division codes."""

    PATTERN = re.compile(
        r"^\d{2}\s?\d{2}\s?\d{2}(\.\d{2})?$",
    )
    VALID_DIVISIONS: ClassVar[set[str]] = {f"{i:02d}" for i in range(51)}

    def validate(self, csi_code: str) -> tuple[bool, str]:
        """Check if CSI code is valid format and division."""
        if not self.PATTERN.match(csi_code):
            return False, f"Invalid CSI format: {csi_code}"
        division = csi_code[:2]
        if division not in self.VALID_DIVISIONS:
            return False, f"Invalid CSI division: {division}"
        return True, ""


class OSHACitationValidator:
    """Validate OSHA CFR citations."""

    CITATION_PATTERN = re.compile(
        r"^29\s*CFR\s*1926\.\d{1,4}(\([a-z]\))?$",
    )
    KNOWN_SUBPARTS: ClassVar[set[str]] = {
        "1926.20",
        "1926.21",
        "1926.100",
        "1926.102",
        "1926.451",
        "1926.452",
        "1926.501",
        "1926.502",
        "1926.503",
        "1926.550",
        "1926.600",
        "1926.651",
        "1926.652",
        "1926.701",
        "1926.702",
        "1926.750",
        "1926.751",
        "1926.760",
        "1926.761",
    }

    async def validate(
        self,
        citation: str,
    ) -> tuple[bool, str]:
        """Check if OSHA citation is valid format."""
        if not self.CITATION_PATTERN.match(citation):
            return False, f"Invalid OSHA citation format: {citation}"
        # Extract the section number
        parts = citation.replace("29 CFR ", "").replace("29CFR", "")
        section = parts.split("(")[0].strip()
        if section in self.KNOWN_SUBPARTS:
            return True, ""
        return True, f"Citation {citation} not in known list"


class RSMeansCostRangeValidator:
    """Validate cost estimates against RSMeans benchmarks."""

    # Simplified RSMeans benchmarks ($/unit) by CSI division
    BENCHMARKS: ClassVar[dict[str, dict]] = {
        "03": {
            "description": "Concrete",
            "unit": "cy",
            "low": 150.0,
            "high": 800.0,
        },
        "05": {
            "description": "Metals",
            "unit": "ton",
            "low": 2000.0,
            "high": 8000.0,
        },
        "09": {
            "description": "Finishes",
            "unit": "sf",
            "low": 2.0,
            "high": 50.0,
        },
        "31": {
            "description": "Earthwork",
            "unit": "cy",
            "low": 5.0,
            "high": 80.0,
        },
    }

    def validate(
        self,
        csi_division: str,
        unit_cost: float,
        tolerance: float = 0.30,
    ) -> tuple[bool, str]:
        """Check if unit cost is within tolerance of RSMeans."""
        div = csi_division[:2]
        benchmark = self.BENCHMARKS.get(div)
        if not benchmark:
            return True, ""

        low = benchmark["low"] * (1 - tolerance)
        high = benchmark["high"] * (1 + tolerance)

        if unit_cost < low:
            return False, (
                f"Cost ${unit_cost:.2f}/{benchmark['unit']} below RSMeans range for div {div}"
            )
        if unit_cost > high:
            return False, (
                f"Cost ${unit_cost:.2f}/{benchmark['unit']} above RSMeans range for div {div}"
            )
        return True, ""


async def validate_domain(
    parsed_output: dict,
    agent_name: str,
) -> dict:
    """Run domain-specific validators on agent output."""
    violations = []

    # Check CSI codes if present
    csi_validator = CSIMasterFormatValidator()
    for key in ("csi_code", "division_code", "masterformat_code"):
        if key in parsed_output:
            valid, msg = csi_validator.validate(
                str(parsed_output[key]),
            )
            if not valid:
                violations.append(
                    {
                        "stage": "domain_rules",
                        "rule": "csi_masterformat",
                        "message": msg,
                        "severity": "error",
                    }
                )

    # Check OSHA citations if present
    osha_validator = OSHACitationValidator()
    for key in ("osha_citation", "regulation_code", "cfr_reference"):
        if key in parsed_output:
            valid, msg = await osha_validator.validate(
                str(parsed_output[key]),
            )
            if not valid:
                violations.append(
                    {
                        "stage": "domain_rules",
                        "rule": "osha_citation",
                        "message": msg,
                        "severity": "error",
                    }
                )

    # Check cost ranges if present
    cost_validator = RSMeansCostRangeValidator()
    if "unit_cost" in parsed_output and "csi_code" in parsed_output:
        valid, msg = cost_validator.validate(
            str(parsed_output["csi_code"]),
            float(parsed_output["unit_cost"]),
        )
        if not valid:
            violations.append(
                {
                    "stage": "domain_rules",
                    "rule": "rsmeans_range",
                    "message": msg,
                    "severity": "warning",
                }
            )

    return {"violations": violations}
