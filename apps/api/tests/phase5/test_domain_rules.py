"""Tests for CSI, OSHA, RSMeans domain validators."""

from __future__ import annotations

from app.services.guardrails.domain_rules import (
    CSIMasterFormatValidator,
    OSHACitationValidator,
    RSMeansCostRangeValidator,
)


class TestCSIValidator:
    def test_valid_code(self):
        v = CSIMasterFormatValidator()
        valid, _msg = v.validate("03 30 00")
        assert valid is True

    def test_valid_code_no_spaces(self):
        v = CSIMasterFormatValidator()
        valid, _msg = v.validate("033000")
        assert valid is True

    def test_valid_code_with_suffix(self):
        v = CSIMasterFormatValidator()
        valid, _msg = v.validate("03 30 00.13")
        assert valid is True

    def test_invalid_division(self):
        v = CSIMasterFormatValidator()
        valid, msg = v.validate("99 00 00")
        assert valid is False
        assert "division" in msg.lower()

    def test_invalid_format(self):
        v = CSIMasterFormatValidator()
        valid, msg = v.validate("abc")
        assert valid is False
        assert "format" in msg.lower()


class TestOSHAValidator:
    async def test_valid_citation(self):
        v = OSHACitationValidator()
        valid, _msg = await v.validate("29 CFR 1926.502")
        assert valid is True

    async def test_valid_with_subsection(self):
        v = OSHACitationValidator()
        valid, _msg = await v.validate("29 CFR 1926.502(a)")
        assert valid is True

    async def test_invalid_format(self):
        v = OSHACitationValidator()
        valid, _msg = await v.validate("OSHA 123")
        assert valid is False

    async def test_known_subpart(self):
        v = OSHACitationValidator()
        valid, _msg = await v.validate("29 CFR 1926.451")
        assert valid is True


class TestRSMeansValidator:
    def test_cost_in_range(self):
        v = RSMeansCostRangeValidator()
        valid, _msg = v.validate("03", 400.0)
        assert valid is True

    def test_cost_above_range(self):
        v = RSMeansCostRangeValidator()
        valid, msg = v.validate("03", 2000.0)
        assert valid is False
        assert "above" in msg.lower()

    def test_cost_below_range(self):
        v = RSMeansCostRangeValidator()
        valid, msg = v.validate("03", 10.0)
        assert valid is False
        assert "below" in msg.lower()

    def test_unknown_division(self):
        v = RSMeansCostRangeValidator()
        valid, _msg = v.validate("99", 100.0)
        assert valid is True

    def test_tolerance_parameter(self):
        v = RSMeansCostRangeValidator()
        # With 50% tolerance, wider range accepted
        valid, _msg = v.validate("03", 1100.0, tolerance=0.50)
        assert valid is True
