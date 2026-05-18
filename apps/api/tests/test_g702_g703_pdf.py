"""Unit tests for PDF generation (no DB needed)."""

from __future__ import annotations

from decimal import Decimal

from app.services.controls.pdf_generator import (
    _format_money,
    _format_pct,
    generate_g702_pdf,
    generate_g703_pdf,
)

D = Decimal


class TestFormatHelpers:
    def test_format_money_positive(self):
        assert _format_money(D("1234567.89")) == "$1,234,567.89"

    def test_format_money_zero(self):
        assert _format_money(D("0")) == "$0.00"

    def test_format_money_negative(self):
        assert _format_money(D("-500.00")) == "($500.00)"

    def test_format_money_large(self):
        assert _format_money(D("99999999.99")) == "$99,999,999.99"

    def test_format_pct(self):
        assert _format_pct(D("95.5000")) == "95.50%"

    def test_format_pct_zero(self):
        assert _format_pct(D("0")) == "0.00%"

    def test_format_pct_hundred(self):
        assert _format_pct(D("100.0000")) == "100.00%"


class TestG702PDF:
    def test_returns_valid_pdf(self):
        pay_app_data = {
            "application_number": 1,
            "period_to": "2025-06-30",
            "original_contract_sum": "1000000.00",
            "net_change_by_cos": "50000.00",
            "contract_sum_to_date": "1050000.00",
            "total_completed_and_stored": "300000.00",
            "retainage_pct": "10",
            "retainage_work_completed": "25000.00",
            "retainage_stored_materials": "5000.00",
            "total_retainage": "30000.00",
            "total_earned_less_retainage": "270000.00",
            "less_previous_certificates": "100000.00",
            "current_payment_due": "170000.00",
            "balance_to_finish_including_retainage": "780000.00",
        }
        pdf_bytes = generate_g702_pdf(
            pay_app_data,
            project_name="Test Office Building",
            contractor_name="ABC Construction Inc.",
            architect_name="Smith Architects LLC",
        )
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_g702_with_zero_values(self):
        pay_app_data = {
            "application_number": 1,
            "period_to": "2025-01-31",
            "original_contract_sum": "0",
            "net_change_by_cos": "0",
            "contract_sum_to_date": "0",
            "total_completed_and_stored": "0",
            "retainage_pct": "10",
            "retainage_work_completed": "0",
            "retainage_stored_materials": "0",
            "total_retainage": "0",
            "total_earned_less_retainage": "0",
            "less_previous_certificates": "0",
            "current_payment_due": "0",
            "balance_to_finish_including_retainage": "0",
        }
        pdf_bytes = generate_g702_pdf(pay_app_data, project_name="Empty Project")
        assert pdf_bytes[:4] == b"%PDF"

    def test_g702_with_negative_payment(self):
        pay_app_data = {
            "application_number": 3,
            "period_to": "2025-09-30",
            "original_contract_sum": "500000.00",
            "net_change_by_cos": "-10000.00",
            "contract_sum_to_date": "490000.00",
            "total_completed_and_stored": "100000.00",
            "retainage_pct": "10",
            "retainage_work_completed": "10000.00",
            "retainage_stored_materials": "0",
            "total_retainage": "10000.00",
            "total_earned_less_retainage": "90000.00",
            "less_previous_certificates": "95000.00",
            "current_payment_due": "-5000.00",
            "balance_to_finish_including_retainage": "400000.00",
        }
        pdf_bytes = generate_g702_pdf(pay_app_data, project_name="Overrun Project")
        assert pdf_bytes[:4] == b"%PDF"


class TestG703PDF:
    def test_returns_valid_pdf(self):
        line_items = [
            {
                "item_number": "1",
                "description_of_work": "Sitework",
                "scheduled_value": "200000.00",
                "work_completed_previous": "50000.00",
                "work_completed_this_period": "30000.00",
                "materials_presently_stored": "10000.00",
                "total_completed_and_stored": "90000.00",
                "percent_complete": "45.0000",
                "balance_to_finish": "110000.00",
            },
            {
                "item_number": "2",
                "description_of_work": "Concrete Foundation",
                "scheduled_value": "300000.00",
                "work_completed_previous": "0",
                "work_completed_this_period": "75000.00",
                "materials_presently_stored": "0",
                "total_completed_and_stored": "75000.00",
                "percent_complete": "25.0000",
                "balance_to_finish": "225000.00",
            },
        ]
        pay_app_data = {
            "application_number": 1,
            "period_to": "2025-06-30",
        }
        pdf_bytes = generate_g703_pdf(line_items, pay_app_data, "Test Project")
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_g703_multi_page(self):
        """50 line items should produce a multi-page PDF."""
        line_items = []
        for i in range(50):
            line_items.append(
                {
                    "item_number": str(i + 1),
                    "description_of_work": f"Work Item {i + 1} - Detailed Description",
                    "scheduled_value": "10000.00",
                    "work_completed_previous": "2000.00",
                    "work_completed_this_period": "1000.00",
                    "materials_presently_stored": "500.00",
                    "total_completed_and_stored": "3500.00",
                    "percent_complete": "35.0000",
                    "balance_to_finish": "6500.00",
                }
            )
        pay_app_data = {"application_number": 2, "period_to": "2025-07-31"}
        pdf_bytes = generate_g703_pdf(line_items, pay_app_data, "Large Project")
        assert pdf_bytes[:4] == b"%PDF"
        # Multi-page should be larger than single-page
        assert len(pdf_bytes) > 2000

    def test_g703_empty_line_items(self):
        """Edge case: empty line items list."""
        pay_app_data = {"application_number": 1, "period_to": "2025-06-30"}
        pdf_bytes = generate_g703_pdf([], pay_app_data, "Empty Project")
        assert pdf_bytes[:4] == b"%PDF"
