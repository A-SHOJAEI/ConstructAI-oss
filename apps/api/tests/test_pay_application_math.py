"""Unit tests for G702/G703 math computations — no DB needed.

Every mathematical relationship on the AIA G702 and G703 forms is verified here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.controls.pay_application_math import (
    compute_g702_totals,
    compute_g703_line,
    compute_pco_total,
    validate_no_overbilling,
)

D = Decimal  # shorthand


# ---------------------------------------------------------------------------
# PCO Total Cost
# ---------------------------------------------------------------------------


class TestComputePCOTotal:
    def test_basic_markup(self):
        result = compute_pco_total(
            labor=D("5000"),
            material=D("2000"),
            equipment=D("8000"),
            subcontractor=D("0"),
            overhead=D("1500"),
            profit_markup_pct=D("10"),
        )
        # subtotal = 16500, markup = 16500 * 1.10 = 18150
        assert result == D("18150.00")

    def test_zero_markup(self):
        result = compute_pco_total(
            labor=D("1000"),
            material=D("500"),
            equipment=D("0"),
            subcontractor=D("200"),
            overhead=D("300"),
            profit_markup_pct=D("0"),
        )
        assert result == D("2000.00")

    def test_large_markup(self):
        result = compute_pco_total(
            labor=D("10000"),
            material=D("5000"),
            equipment=D("3000"),
            subcontractor=D("7000"),
            overhead=D("2000"),
            profit_markup_pct=D("25"),
        )
        # subtotal = 27000, * 1.25 = 33750
        assert result == D("33750.00")

    def test_all_zeros(self):
        result = compute_pco_total(
            labor=D("0"),
            material=D("0"),
            equipment=D("0"),
            subcontractor=D("0"),
            overhead=D("0"),
            profit_markup_pct=D("15"),
        )
        assert result == D("0.00")

    def test_fractional_costs(self):
        result = compute_pco_total(
            labor=D("1000.50"),
            material=D("500.25"),
            equipment=D("0"),
            subcontractor=D("0"),
            overhead=D("0"),
            profit_markup_pct=D("10"),
        )
        # subtotal = 1500.75, * 1.10 = 1650.825 -> rounded to 1650.83
        assert result == D("1650.83")


# ---------------------------------------------------------------------------
# G703 Line Item Math (Columns G, H, I)
# ---------------------------------------------------------------------------


class TestG703LineMath:
    def test_basic_computation(self):
        result = compute_g703_line(
            scheduled_value=D("100000.00"),
            work_completed_previous=D("30000.00"),
            work_completed_this_period=D("15000.00"),
            materials_presently_stored=D("5000.00"),
        )
        # G = 30000 + 15000 + 5000 = 50000
        assert result["total_completed_and_stored"] == D("50000.00")
        # H = 50000 / 100000 * 100 = 50.0000
        assert result["percent_complete"] == D("50.0000")
        # I = 100000 - 50000 = 50000
        assert result["balance_to_finish"] == D("50000.00")

    def test_zero_scheduled_value_no_division_error(self):
        result = compute_g703_line(
            scheduled_value=D("0"),
            work_completed_previous=D("0"),
            work_completed_this_period=D("0"),
            materials_presently_stored=D("0"),
        )
        assert result["percent_complete"] == D("0.0000")
        assert result["total_completed_and_stored"] == D("0.00")
        assert result["balance_to_finish"] == D("0.00")

    def test_hundred_percent_complete(self):
        result = compute_g703_line(
            scheduled_value=D("50000.00"),
            work_completed_previous=D("45000.00"),
            work_completed_this_period=D("5000.00"),
            materials_presently_stored=D("0"),
        )
        assert result["percent_complete"] == D("100.0000")
        assert result["balance_to_finish"] == D("0.00")

    def test_no_work_this_period(self):
        result = compute_g703_line(
            scheduled_value=D("200000.00"),
            work_completed_previous=D("80000.00"),
            work_completed_this_period=D("0"),
            materials_presently_stored=D("0"),
        )
        assert result["total_completed_and_stored"] == D("80000.00")
        assert result["percent_complete"] == D("40.0000")
        assert result["balance_to_finish"] == D("120000.00")

    def test_materials_stored_only(self):
        result = compute_g703_line(
            scheduled_value=D("100000.00"),
            work_completed_previous=D("0"),
            work_completed_this_period=D("0"),
            materials_presently_stored=D("25000.00"),
        )
        assert result["total_completed_and_stored"] == D("25000.00")
        assert result["percent_complete"] == D("25.0000")
        assert result["balance_to_finish"] == D("75000.00")

    def test_column_g_equals_d_plus_e_plus_f(self):
        """Core identity: G = D + E + F."""
        d = D("12345.67")
        e = D("8901.23")
        f = D("4567.89")
        result = compute_g703_line(
            scheduled_value=D("999999.99"),
            work_completed_previous=d,
            work_completed_this_period=e,
            materials_presently_stored=f,
        )
        assert result["total_completed_and_stored"] == d + e + f

    def test_column_i_equals_c_minus_g(self):
        """Core identity: I = C - G."""
        c = D("500000.00")
        result = compute_g703_line(
            scheduled_value=c,
            work_completed_previous=D("100000.00"),
            work_completed_this_period=D("50000.00"),
            materials_presently_stored=D("20000.00"),
        )
        g = result["total_completed_and_stored"]
        assert result["balance_to_finish"] == c - g

    def test_column_h_equals_g_over_c(self):
        """Core identity: H = G / C * 100."""
        result = compute_g703_line(
            scheduled_value=D("300000.00"),
            work_completed_previous=D("60000.00"),
            work_completed_this_period=D("30000.00"),
            materials_presently_stored=D("10000.00"),
        )
        g = result["total_completed_and_stored"]  # 100000
        expected_pct = g / D("300000.00") * D("100")
        assert result["percent_complete"] == expected_pct.quantize(D("0.0001"))

    def test_negative_balance_when_overbilled(self):
        """Column I can be negative if G > C (overbilling scenario)."""
        result = compute_g703_line(
            scheduled_value=D("10000.00"),
            work_completed_previous=D("8000.00"),
            work_completed_this_period=D("3000.00"),
            materials_presently_stored=D("0"),
        )
        assert result["total_completed_and_stored"] == D("11000.00")
        assert result["balance_to_finish"] == D("-1000.00")
        assert result["percent_complete"] == D("110.0000")


# ---------------------------------------------------------------------------
# G702 Totals (Summary Sheet)
# ---------------------------------------------------------------------------


class TestG702Totals:
    @pytest.fixture
    def two_line_items(self):
        return [
            {
                "item_number": "1",
                "scheduled_value": D("500000.00"),
                "work_completed_previous": D("100000.00"),
                "work_completed_this_period": D("50000.00"),
                "materials_presently_stored": D("20000.00"),
                "retainage_pct": D("10.00"),
            },
            {
                "item_number": "2",
                "scheduled_value": D("300000.00"),
                "work_completed_previous": D("60000.00"),
                "work_completed_this_period": D("30000.00"),
                "materials_presently_stored": D("10000.00"),
                "retainage_pct": D("10.00"),
            },
        ]

    def test_basic_g702_computation(self, two_line_items):
        result = compute_g702_totals(
            line_items=two_line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("144000.00"),
            original_contract_sum=D("750000.00"),
            net_change_by_cos=D("50000.00"),
        )
        # Line 3: 750000 + 50000 = 800000
        assert result["contract_sum_to_date"] == D("800000.00")
        # Line 4: (100+50+20) + (60+30+10) = 270000
        assert result["total_completed_and_stored"] == D("270000.00")
        # Retainage on work: (150000 + 90000) * 0.10 = 24000
        assert result["retainage_work_completed"] == D("24000.00")
        # Retainage on stored: (20000 + 10000) * 0.10 = 3000
        assert result["retainage_stored_materials"] == D("3000.00")
        # Line 5: 24000 + 3000 = 27000
        assert result["total_retainage"] == D("27000.00")
        # Line 6: 270000 - 27000 = 243000
        assert result["total_earned_less_retainage"] == D("243000.00")
        # Line 8: 243000 - 144000 = 99000
        assert result["current_payment_due"] == D("99000.00")
        # Line 9: 800000 - 270000 + 27000 = 557000
        assert result["balance_to_finish_including_retainage"] == D("557000.00")

    def test_contract_sum_to_date_identity(self, two_line_items):
        """Line 3 = Line 1 + Line 2."""
        result = compute_g702_totals(
            line_items=two_line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("800000.00"),
            net_change_by_cos=D("25000.00"),
        )
        assert result["contract_sum_to_date"] == D("800000.00") + D("25000.00")

    def test_earned_less_retainage_identity(self, two_line_items):
        """Line 6 = Line 4 - Line 5."""
        result = compute_g702_totals(
            line_items=two_line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("800000.00"),
            net_change_by_cos=D("0"),
        )
        assert result["total_earned_less_retainage"] == (
            result["total_completed_and_stored"] - result["total_retainage"]
        )

    def test_current_payment_due_identity(self, two_line_items):
        """Line 8 = Line 6 - Line 7."""
        prev_certs = D("100000.00")
        result = compute_g702_totals(
            line_items=two_line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=prev_certs,
            original_contract_sum=D("800000.00"),
            net_change_by_cos=D("0"),
        )
        assert result["current_payment_due"] == (result["total_earned_less_retainage"] - prev_certs)

    def test_balance_to_finish_identity(self, two_line_items):
        """Line 9 = Line 3 - Line 4 + Line 5."""
        result = compute_g702_totals(
            line_items=two_line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("800000.00"),
            net_change_by_cos=D("0"),
        )
        assert result["balance_to_finish_including_retainage"] == (
            result["contract_sum_to_date"]
            - result["total_completed_and_stored"]
            + result["total_retainage"]
        )

    def test_variable_retainage_per_line(self):
        """Different retainage rates per line item."""
        line_items = [
            {
                "item_number": "1",
                "scheduled_value": D("100000.00"),
                "work_completed_previous": D("50000.00"),
                "work_completed_this_period": D("10000.00"),
                "materials_presently_stored": D("5000.00"),
                "retainage_pct": D("10.00"),  # 10%
            },
            {
                "item_number": "2",
                "scheduled_value": D("200000.00"),
                "work_completed_previous": D("40000.00"),
                "work_completed_this_period": D("20000.00"),
                "materials_presently_stored": D("10000.00"),
                "retainage_pct": D("5.00"),  # 5%
            },
        ]
        result = compute_g702_totals(
            line_items=line_items,
            retainage_pct=D("10.00"),  # header-level fallback
            less_previous_certificates=D("0"),
            original_contract_sum=D("300000.00"),
            net_change_by_cos=D("0"),
        )
        # Line 1 work retainage: (50000+10000)*0.10 = 6000
        # Line 2 work retainage: (40000+20000)*0.05 = 3000
        assert result["retainage_work_completed"] == D("9000.00")
        # Line 1 stored retainage: 5000*0.10 = 500
        # Line 2 stored retainage: 10000*0.05 = 500
        assert result["retainage_stored_materials"] == D("1000.00")
        assert result["total_retainage"] == D("10000.00")

    def test_fallback_to_header_retainage(self):
        """When line item has no retainage_pct, use header-level."""
        line_items = [
            {
                "item_number": "1",
                "scheduled_value": D("100000.00"),
                "work_completed_previous": D("40000.00"),
                "work_completed_this_period": D("10000.00"),
                "materials_presently_stored": D("0"),
                # No retainage_pct key
            },
        ]
        result = compute_g702_totals(
            line_items=line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("100000.00"),
            net_change_by_cos=D("0"),
        )
        # work retainage: 50000 * 0.10 = 5000
        assert result["retainage_work_completed"] == D("5000.00")

    def test_zero_line_items(self):
        """Edge case: no line items."""
        result = compute_g702_totals(
            line_items=[],
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("500000.00"),
            net_change_by_cos=D("0"),
        )
        assert result["contract_sum_to_date"] == D("500000.00")
        assert result["total_completed_and_stored"] == D("0.00")
        assert result["total_retainage"] == D("0.00")
        assert result["current_payment_due"] == D("0.00")
        assert result["balance_to_finish_including_retainage"] == D("500000.00")

    def test_negative_current_payment_due(self):
        """Previous certificates exceed current earned → negative payment."""
        line_items = [
            {
                "item_number": "1",
                "scheduled_value": D("100000.00"),
                "work_completed_previous": D("10000.00"),
                "work_completed_this_period": D("5000.00"),
                "materials_presently_stored": D("0"),
                "retainage_pct": D("10.00"),
            },
        ]
        result = compute_g702_totals(
            line_items=line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("20000.00"),  # more than earned
            original_contract_sum=D("100000.00"),
            net_change_by_cos=D("0"),
        )
        # earned less retainage = 15000 - 1500 = 13500
        # current_payment = 13500 - 20000 = -6500
        assert result["current_payment_due"] == D("-6500.00")

    def test_first_pay_app_zero_previous(self):
        """First pay application: no previous certificates."""
        line_items = [
            {
                "item_number": "1",
                "scheduled_value": D("1000000.00"),
                "work_completed_previous": D("0"),
                "work_completed_this_period": D("150000.00"),
                "materials_presently_stored": D("50000.00"),
                "retainage_pct": D("10.00"),
            },
        ]
        result = compute_g702_totals(
            line_items=line_items,
            retainage_pct=D("10.00"),
            less_previous_certificates=D("0"),
            original_contract_sum=D("1000000.00"),
            net_change_by_cos=D("0"),
        )
        # total completed = 200000
        assert result["total_completed_and_stored"] == D("200000.00")
        # retainage work = 150000 * 0.10 = 15000
        # retainage stored = 50000 * 0.10 = 5000
        assert result["total_retainage"] == D("20000.00")
        # earned less retainage = 200000 - 20000 = 180000
        assert result["total_earned_less_retainage"] == D("180000.00")
        # current payment = 180000 - 0 = 180000
        assert result["current_payment_due"] == D("180000.00")
        # balance = 1000000 - 200000 + 20000 = 820000
        assert result["balance_to_finish_including_retainage"] == D("820000.00")


# ---------------------------------------------------------------------------
# Overbilling Validation
# ---------------------------------------------------------------------------


class TestOverbillingValidation:
    def test_no_overbilling(self):
        warnings = validate_no_overbilling(
            [
                {
                    "item_number": "1",
                    "scheduled_value": D("100000"),
                    "work_completed_previous": D("50000"),
                    "work_completed_this_period": D("20000"),
                    "materials_presently_stored": D("0"),
                }
            ]
        )
        assert warnings == []

    def test_exactly_at_scheduled_value(self):
        """Billing exactly 100% is NOT overbilling."""
        warnings = validate_no_overbilling(
            [
                {
                    "item_number": "1",
                    "scheduled_value": D("100000"),
                    "work_completed_previous": D("80000"),
                    "work_completed_this_period": D("20000"),
                    "materials_presently_stored": D("0"),
                }
            ]
        )
        assert warnings == []

    def test_overbilling_detected(self):
        warnings = validate_no_overbilling(
            [
                {
                    "item_number": "1",
                    "scheduled_value": D("100000"),
                    "work_completed_previous": D("90000"),
                    "work_completed_this_period": D("15000"),
                    "materials_presently_stored": D("0"),
                }
            ]
        )
        assert len(warnings) == 1
        assert warnings[0]["item_number"] == "1"
        assert warnings[0]["excess"] == D("5000.00")
        assert warnings[0]["scheduled"] == D("100000")
        assert warnings[0]["billed"] == D("105000.00")

    def test_overbilling_via_stored_materials(self):
        """Stored materials can push total over scheduled value."""
        warnings = validate_no_overbilling(
            [
                {
                    "item_number": "1",
                    "scheduled_value": D("50000"),
                    "work_completed_previous": D("30000"),
                    "work_completed_this_period": D("10000"),
                    "materials_presently_stored": D("15000"),
                }
            ]
        )
        assert len(warnings) == 1
        assert warnings[0]["excess"] == D("5000.00")

    def test_multiple_lines_mixed(self):
        """Some lines overbilled, some not."""
        warnings = validate_no_overbilling(
            [
                {
                    "item_number": "1",
                    "scheduled_value": D("100000"),
                    "work_completed_previous": D("50000"),
                    "work_completed_this_period": D("20000"),
                    "materials_presently_stored": D("0"),
                },
                {
                    "item_number": "2",
                    "scheduled_value": D("50000"),
                    "work_completed_previous": D("40000"),
                    "work_completed_this_period": D("15000"),
                    "materials_presently_stored": D("0"),
                },
                {
                    "item_number": "3",
                    "scheduled_value": D("75000"),
                    "work_completed_previous": D("75000"),
                    "work_completed_this_period": D("0"),
                    "materials_presently_stored": D("0"),
                },
            ]
        )
        assert len(warnings) == 1
        assert warnings[0]["item_number"] == "2"

    def test_empty_line_items(self):
        warnings = validate_no_overbilling([])
        assert warnings == []
