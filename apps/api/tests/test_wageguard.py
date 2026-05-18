"""WageGuard: Comprehensive tests for Davis-Bacon prevailing wage compliance.

Tests cover models, service functions, and API endpoints.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from app.models.project import Project
from app.models.wage_compliance import WageDetermination

from app.services.products.wageguard.service import (
    KNOWN_CLASSIFICATIONS,
    SEED_DETERMINATIONS,
    add_line_item,
    configure_project,
    create_payroll,
    generate_audit_package,
    get_apprenticeship_status,
    map_classification,
    search_determinations,
    seed_determinations,
    update_payroll_status,
    validate_payroll,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for wage compliance tests."""
    project = Project(name="WageGuard Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def seeded_db(db_session):
    """Seed the database with sample wage determinations."""
    count = await seed_determinations(db_session)
    assert count == 3
    return db_session


@pytest_asyncio.fixture
async def va_determination(seeded_db):
    """Return the VA wage determination after seeding."""
    from sqlalchemy import select

    result = await seeded_db.execute(
        select(WageDetermination).where(WageDetermination.sam_gov_id == "VA20240001")
    )
    return result.scalar_one()


@pytest_asyncio.fixture
async def configured_project(db_session, test_project, test_org, va_determination):
    """A project with wage config linked to the VA determination."""
    await configure_project(
        db_session,
        test_project.id,
        test_org.id,
        {
            "wage_determination_id": va_determination.id,
            "project_type": "traditional_federal",
            "apprenticeship_required": True,
        },
    )
    return test_project


@pytest_asyncio.fixture
async def sample_payroll(db_session, configured_project, test_org):
    """A draft payroll with one compliant line item."""
    payroll = await create_payroll(
        db_session,
        configured_project.id,
        test_org.id,
        "ACME Builders",
        date(2025, 1, 11),
    )
    await add_line_item(
        db_session,
        payroll.id,
        configured_project.id,
        {
            "worker_name": "John Smith",
            "classification": "Carpenter",
            "hours_straight": 40,
            "hours_overtime": 0,
            "rate_paid": 30.00,
            "fringe_paid": 16.00,
        },
    )
    return payroll


# ===================================================================
# TestWageDeterminationModel
# ===================================================================


class TestWageDeterminationModel:
    """Tests for the WageDetermination model."""

    async def test_create_determination(self, db_session):
        """A WageDetermination can be created with required fields."""
        wd = WageDetermination(
            state="NY",
            county="Kings",
            project_type="building",
            classifications=[
                {"title": "Carpenter", "base_rate": 40.0, "fringe_rate": 20.0, "total_rate": 60.0}
            ],
        )
        db_session.add(wd)
        await db_session.flush()
        assert wd.id is not None

    async def test_determination_with_sam_id(self, db_session):
        """sam_gov_id is optional and nullable."""
        wd = WageDetermination(
            sam_gov_id="NY20240099",
            state="NY",
            county="New York",
            project_type="heavy",
            classifications=[],
        )
        db_session.add(wd)
        await db_session.flush()
        assert wd.sam_gov_id == "NY20240099"

    async def test_seed_data_structure(self):
        """Verify seed data has the expected structure."""
        assert len(SEED_DETERMINATIONS) == 3
        va = SEED_DETERMINATIONS[0]
        assert va["state"] == "VA"
        assert len(va["classifications"]) == 10
        for c in va["classifications"]:
            assert "title" in c
            assert "base_rate" in c
            assert "fringe_rate" in c
            assert "total_rate" in c

    async def test_classification_rate_consistency(self):
        """Verify base + fringe = total in seed data."""
        for det in SEED_DETERMINATIONS:
            for c in det["classifications"]:
                expected_total = round(c["base_rate"] + c["fringe_rate"], 2)
                assert c["total_rate"] == expected_total, (
                    f"{det['state']} {c['title']}: "
                    f"{c['base_rate']}+{c['fringe_rate']} != {c['total_rate']}"
                )


# ===================================================================
# TestSearchDeterminations
# ===================================================================


class TestSearchDeterminations:
    """Tests for searching wage determinations."""

    async def test_search_all(self, seeded_db):
        """Search with no filters returns all determinations."""
        results = await search_determinations(seeded_db)
        assert len(results) == 3

    async def test_search_by_state(self, seeded_db):
        """Filter by state returns only matching determinations."""
        results = await search_determinations(seeded_db, state="CA")
        assert len(results) == 1
        assert results[0].state == "CA"

    async def test_search_by_county(self, seeded_db):
        """Filter by county returns only matching determinations."""
        results = await search_determinations(seeded_db, county="Harris")
        assert len(results) == 1
        assert results[0].state == "TX"

    async def test_search_by_project_type(self, seeded_db):
        """Filter by project_type."""
        results = await search_determinations(seeded_db, project_type="building")
        assert len(results) == 3

    async def test_search_no_results(self, seeded_db):
        """Search with non-matching filter returns empty."""
        results = await search_determinations(seeded_db, state="ZZ")
        assert len(results) == 0


# ===================================================================
# TestSeedDeterminations
# ===================================================================


class TestSeedDeterminations:
    """Tests for seed determination insertion."""

    async def test_seed_inserts(self, db_session):
        """Seeding inserts all determinations."""
        count = await seed_determinations(db_session)
        assert count == 3

    async def test_seed_idempotent(self, seeded_db):
        """Seeding again does not duplicate records."""
        count = await seed_determinations(seeded_db)
        assert count == 0
        all_dets = await search_determinations(seeded_db)
        assert len(all_dets) == 3

    async def test_seed_partial_idempotent(self, db_session):
        """Seeding with one existing record only inserts new ones."""
        # Insert VA manually
        wd = WageDetermination(
            sam_gov_id="VA20240001",
            state="VA",
            county="Fairfax",
            project_type="building",
            classifications=[],
        )
        db_session.add(wd)
        await db_session.flush()

        count = await seed_determinations(db_session)
        assert count == 2  # Only CA and TX inserted


# ===================================================================
# TestConfigureProject
# ===================================================================


class TestConfigureProject:
    """Tests for project wage configuration."""

    async def test_create_config(self, db_session, test_project, test_org):
        """Creating a config for a project works."""
        config = await configure_project(
            db_session,
            test_project.id,
            test_org.id,
            {"project_type": "ira_eligible"},
        )
        assert config.project_type == "ira_eligible"
        assert config.project_id == test_project.id

    async def test_update_config(self, db_session, test_project, test_org):
        """Updating an existing config upserts the fields."""
        config = await configure_project(
            db_session,
            test_project.id,
            test_org.id,
            {"project_type": "traditional_federal"},
        )
        updated = await configure_project(
            db_session,
            test_project.id,
            test_org.id,
            {"project_type": "ira_eligible", "apprenticeship_required": True},
        )
        assert updated.id == config.id
        assert updated.project_type == "ira_eligible"
        assert updated.apprenticeship_required is True

    async def test_config_defaults(self, db_session, test_project, test_org):
        """Default values are applied when not specified."""
        config = await configure_project(
            db_session,
            test_project.id,
            test_org.id,
            {},
        )
        assert config.apprenticeship_required is False
        assert config.apprenticeship_pct == Decimal("0.1500")

    async def test_config_with_determination(
        self, db_session, test_project, test_org, va_determination
    ):
        """Config can reference a wage determination."""
        config = await configure_project(
            db_session,
            test_project.id,
            test_org.id,
            {"wage_determination_id": va_determination.id},
        )
        assert config.wage_determination_id == va_determination.id


# ===================================================================
# TestCreatePayroll
# ===================================================================


class TestCreatePayroll:
    """Tests for payroll creation."""

    async def test_create_payroll(self, db_session, test_project, test_org):
        """Creating a payroll assigns number 1."""
        payroll = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 1, 11)
        )
        assert payroll.payroll_number == 1
        assert payroll.contractor_name == "ACME"
        assert payroll.status == "draft"

    async def test_payroll_number_increments(self, db_session, test_project, test_org):
        """Subsequent payrolls for the same contractor increment."""
        p1 = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 1, 11)
        )
        p2 = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 1, 18)
        )
        assert p1.payroll_number == 1
        assert p2.payroll_number == 2

    async def test_payroll_number_per_contractor(self, db_session, test_project, test_org):
        """Different contractors have independent numbering."""
        p1 = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 1, 11)
        )
        p2 = await create_payroll(
            db_session, test_project.id, test_org.id, "Beta Corp", date(2025, 1, 11)
        )
        assert p1.payroll_number == 1
        assert p2.payroll_number == 1

    async def test_payroll_week_ending(self, db_session, test_project, test_org):
        """Week ending date is stored correctly."""
        payroll = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 3, 15)
        )
        assert payroll.week_ending == date(2025, 3, 15)


# ===================================================================
# TestAddLineItem
# ===================================================================


class TestAddLineItem:
    """Tests for adding payroll line items."""

    async def test_add_compliant_line_item(self, db_session, configured_project, test_org):
        """A worker paid at or above prevailing rate is compliant."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        li = await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Jane Doe",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        assert li.compliant is True
        assert li.deficiency_amount == Decimal("0")
        assert li.prevailing_rate == Decimal("28.50")

    async def test_add_underpaid_line_item(self, db_session, configured_project, test_org):
        """A worker paid below prevailing rate is non-compliant with deficiency."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        li = await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Bob Underpaid",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 20.00,
                "fringe_paid": 10.00,
            },
        )
        assert li.compliant is False
        # Prevailing total = 28.50 + 15.20 = 43.70; paid = 30.00
        # Deficiency = (43.70 - 30.00) * 40 = 548.00
        assert li.deficiency_amount == Decimal("548.00")

    async def test_add_line_item_unknown_classification(
        self, db_session, configured_project, test_org
    ):
        """Unknown classification has no prevailing rate, compliance is None."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        li = await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Mystery Worker",
                "classification": "Widget Maker",
                "hours_straight": 40,
                "rate_paid": 50.00,
                "fringe_paid": 20.00,
            },
        )
        assert li.compliant is None
        assert li.prevailing_rate is None

    async def test_add_apprentice_line_item(self, db_session, configured_project, test_org):
        """Apprentice flag and program are stored correctly."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        li = await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Amy Apprentice",
                "classification": "Carpenter",
                "is_apprentice": True,
                "apprentice_program": "Local 22 JATC",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        assert li.is_apprentice is True
        assert li.apprentice_program == "Local 22 JATC"

    async def test_add_line_item_updates_payroll_totals(
        self, db_session, configured_project, test_org
    ):
        """Adding a line item updates the payroll total hours and gross pay."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Worker A",
                "classification": "Laborer",
                "hours_straight": 40,
                "hours_overtime": 8,
                "rate_paid": 20.00,
                "fringe_paid": 13.00,
            },
        )
        await db_session.refresh(payroll)
        assert payroll.total_hours == Decimal("48")
        # gross = 20.00 * 48 = 960.00
        assert payroll.total_gross_pay == Decimal("960.00")

    async def test_add_line_item_no_config(self, db_session, test_project, test_org):
        """Line item without project config has no prevailing rate."""
        payroll = await create_payroll(
            db_session, test_project.id, test_org.id, "ACME", date(2025, 1, 11)
        )
        li = await add_line_item(
            db_session,
            payroll.id,
            test_project.id,
            {
                "worker_name": "Worker B",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 25.00,
            },
        )
        assert li.prevailing_rate is None
        assert li.compliant is None


# ===================================================================
# TestValidatePayroll
# ===================================================================


class TestValidatePayroll:
    """Tests for payroll validation."""

    async def test_validate_compliant_payroll(self, db_session, sample_payroll):
        """A payroll with all compliant items passes validation."""
        result = await validate_payroll(db_session, sample_payroll.id, sample_payroll.project_id)
        assert result["compliant"] is True
        # May have 'no_apprentice_hours' warning but no errors
        error_flags = [f for f in result["flags"] if f["severity"] == "error"]
        assert len(error_flags) == 0

    async def test_validate_underpayment_flagged(self, db_session, configured_project, test_org):
        """Underpayment is flagged as an error."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "BadPay Inc",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Underpaid Worker",
                "classification": "Electrician",
                "hours_straight": 40,
                "rate_paid": 20.00,
                "fringe_paid": 5.00,
            },
        )
        result = await validate_payroll(db_session, payroll.id, configured_project.id)
        assert result["compliant"] is False
        underpayment_flags = [f for f in result["flags"] if f["type"] == "underpayment"]
        assert len(underpayment_flags) == 1

    async def test_validate_sets_status_flagged(self, db_session, configured_project, test_org):
        """Payroll status is set to 'flagged' when errors are found."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "BadPay Inc",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Underpaid Worker",
                "classification": "Electrician",
                "hours_straight": 40,
                "rate_paid": 20.00,
                "fringe_paid": 5.00,
            },
        )
        await validate_payroll(db_session, payroll.id, configured_project.id)
        await db_session.refresh(payroll)
        assert payroll.status == "flagged"

    async def test_validate_no_apprentice_warning(self, db_session, configured_project, test_org):
        """Missing apprentice hours triggers a warning when required."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "NoApprentice Inc",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Regular Worker",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        result = await validate_payroll(db_session, payroll.id, configured_project.id)
        apprentice_flags = [f for f in result["flags"] if f["type"] == "no_apprentice_hours"]
        assert len(apprentice_flags) == 1
        assert apprentice_flags[0]["severity"] == "warning"

    async def test_validate_unmapped_classification(self, db_session, configured_project, test_org):
        """Unknown classification triggers unmapped_classification warning."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "Custom Corp",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Custom Worker",
                "classification": "Widget Specialist",
                "hours_straight": 40,
                "rate_paid": 50.00,
                "fringe_paid": 20.00,
            },
        )
        result = await validate_payroll(db_session, payroll.id, configured_project.id)
        unmapped = [f for f in result["flags"] if f["type"] == "unmapped_classification"]
        assert len(unmapped) == 1

    async def test_validate_empty_payroll(self, db_session, configured_project, test_org):
        """An empty payroll passes validation (no items to flag)."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "Empty Corp",
            date(2025, 1, 11),
        )
        result = await validate_payroll(db_session, payroll.id, configured_project.id)
        assert result["compliant"] is True
        assert len(result["flags"]) == 0


# ===================================================================
# TestPayrollStatusTransitions
# ===================================================================


class TestPayrollStatusTransitions:
    """Tests for payroll status state machine."""

    async def test_draft_to_submitted(self, db_session, sample_payroll, test_user):
        """Draft payroll can be submitted."""
        payroll = await update_payroll_status(
            db_session,
            sample_payroll.id,
            sample_payroll.project_id,
            "submitted",
            user_id=test_user.id,
        )
        assert payroll.status == "submitted"
        assert payroll.submitted_at is not None

    async def test_submitted_to_accepted(self, db_session, sample_payroll, test_user):
        """Submitted payroll can be accepted."""
        await update_payroll_status(
            db_session,
            sample_payroll.id,
            sample_payroll.project_id,
            "submitted",
        )
        payroll = await update_payroll_status(
            db_session,
            sample_payroll.id,
            sample_payroll.project_id,
            "accepted",
            user_id=test_user.id,
            notes="Looks good",
        )
        assert payroll.status == "accepted"
        assert payroll.reviewed_by == test_user.id
        assert payroll.review_notes == "Looks good"

    async def test_submitted_to_rejected(self, db_session, sample_payroll, test_user):
        """Submitted payroll can be rejected."""
        await update_payroll_status(
            db_session,
            sample_payroll.id,
            sample_payroll.project_id,
            "submitted",
        )
        payroll = await update_payroll_status(
            db_session,
            sample_payroll.id,
            sample_payroll.project_id,
            "rejected",
            user_id=test_user.id,
            notes="Missing SSN data",
        )
        assert payroll.status == "rejected"

    async def test_invalid_transition_raises(self, db_session, sample_payroll):
        """Invalid status transition raises ValueError."""
        with pytest.raises(ValueError, match="Invalid transition"):
            await update_payroll_status(
                db_session,
                sample_payroll.id,
                sample_payroll.project_id,
                "accepted",  # Can't go from draft to accepted
            )

    async def test_flagged_to_submitted(self, db_session, configured_project, test_org):
        """A flagged payroll can still be submitted."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "FlagCo",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Underpaid",
                "classification": "Electrician",
                "hours_straight": 40,
                "rate_paid": 10.00,
                "fringe_paid": 5.00,
            },
        )
        await validate_payroll(db_session, payroll.id, configured_project.id)
        await db_session.refresh(payroll)
        assert payroll.status == "flagged"

        result = await update_payroll_status(
            db_session,
            payroll.id,
            configured_project.id,
            "submitted",
        )
        assert result.status == "submitted"


# ===================================================================
# TestApprenticeshipStatus
# ===================================================================


class TestApprenticeshipStatus:
    """Tests for apprenticeship compliance calculation."""

    async def test_no_payrolls(self, db_session, test_project):
        """No payrolls returns zero hours."""
        status = await get_apprenticeship_status(db_session, test_project.id)
        assert status["total_labor_hours"] == 0.0
        assert status["compliant"] is False

    async def test_compliant_apprenticeship(self, db_session, configured_project, test_org):
        """Project with >= 15% apprentice hours is compliant."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        # 34 hours regular + 6 hours apprentice = 15% apprentice
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Regular",
                "classification": "Carpenter",
                "hours_straight": 34,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Apprentice",
                "classification": "Carpenter",
                "is_apprentice": True,
                "hours_straight": 6,
                "rate_paid": 20.00,
                "fringe_paid": 10.00,
            },
        )
        status = await get_apprenticeship_status(db_session, configured_project.id)
        assert status["total_labor_hours"] == 40.0
        assert status["apprentice_hours"] == 6.0
        assert status["apprentice_pct"] == 0.15
        assert status["compliant"] is True

    async def test_non_compliant_apprenticeship(self, db_session, configured_project, test_org):
        """Project below 15% apprentice hours is not compliant."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Regular",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Apprentice",
                "classification": "Carpenter",
                "is_apprentice": True,
                "hours_straight": 2,
                "rate_paid": 20.00,
                "fringe_paid": 10.00,
            },
        )
        status = await get_apprenticeship_status(db_session, configured_project.id)
        assert status["compliant"] is False
        assert status["hours_deficit"] > 0

    async def test_apprenticeship_across_payrolls(self, db_session, configured_project, test_org):
        """Apprenticeship status aggregates across multiple payrolls."""
        p1 = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        p2 = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 18),
        )
        await add_line_item(
            db_session,
            p1.id,
            configured_project.id,
            {
                "worker_name": "Worker A",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        await add_line_item(
            db_session,
            p2.id,
            configured_project.id,
            {
                "worker_name": "Worker B",
                "classification": "Carpenter",
                "hours_straight": 40,
                "rate_paid": 30.00,
                "fringe_paid": 16.00,
            },
        )
        status = await get_apprenticeship_status(db_session, configured_project.id)
        assert status["total_labor_hours"] == 80.0

    async def test_hours_deficit_calculation(self, db_session, configured_project, test_org):
        """Hours deficit is correctly calculated."""
        payroll = await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME",
            date(2025, 1, 11),
        )
        await add_line_item(
            db_session,
            payroll.id,
            configured_project.id,
            {
                "worker_name": "Worker",
                "classification": "Laborer",
                "hours_straight": 100,
                "rate_paid": 20.00,
                "fringe_paid": 13.00,
            },
        )
        status = await get_apprenticeship_status(db_session, configured_project.id)
        # Need 15% of 100 = 15 hours, have 0, deficit = 15
        assert status["hours_deficit"] == 15.0


# ===================================================================
# TestClassificationMapping
# ===================================================================


class TestClassificationMapping:
    """Tests for Davis-Bacon classification mapping."""

    async def test_fuzzy_match_exact(self):
        """Exact match returns high confidence."""
        result = await map_classification("Carpenter")
        assert result["suggested_davis_bacon"] == "Carpenter"
        assert result["confidence"] >= 0.9

    async def test_fuzzy_match_partial(self):
        """Partial match returns reasonable confidence."""
        result = await map_classification("Finish Carpenter")
        assert result["suggested_davis_bacon"] == "Carpenter"
        assert result["confidence"] > 0.5

    async def test_fuzzy_match_different(self):
        """Very different input still returns best match."""
        result = await map_classification("HVAC Technician")
        # Should match something, confidence should be lower
        assert result["suggested_davis_bacon"] in KNOWN_CLASSIFICATIONS
        assert result["confidence"] > 0

    async def test_known_classifications_list(self):
        """Known classifications list has expected entries."""
        assert "Carpenter" in KNOWN_CLASSIFICATIONS
        assert "Electrician" in KNOWN_CLASSIFICATIONS
        assert "Laborer" in KNOWN_CLASSIFICATIONS
        assert "Operating Engineer" in KNOWN_CLASSIFICATIONS
        assert len(KNOWN_CLASSIFICATIONS) >= 20


# ===================================================================
# TestAuditPackage
# ===================================================================


class TestAuditPackage:
    """Tests for audit package generation."""

    async def test_empty_audit_package(self, db_session, test_project):
        """Empty project returns zero counts."""
        package = await generate_audit_package(db_session, test_project.id)
        assert package["payroll_count"] == 0
        assert package["total_line_items"] == 0
        assert package["compliance_issues"] == 0
        assert package["sub_count"] == 0

    async def test_audit_package_with_data(self, db_session, sample_payroll):
        """Audit package counts payrolls and line items."""
        package = await generate_audit_package(db_session, sample_payroll.project_id)
        assert package["payroll_count"] == 1
        assert package["total_line_items"] == 1
        assert package["sub_count"] == 1
        assert "apprenticeship_status" in package

    async def test_audit_package_multiple_subs(self, db_session, configured_project, test_org):
        """Audit package counts unique subcontractors."""
        await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME Builders",
            date(2025, 1, 11),
        )
        await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "Beta Electric",
            date(2025, 1, 11),
        )
        await create_payroll(
            db_session,
            configured_project.id,
            test_org.id,
            "ACME Builders",
            date(2025, 1, 18),
        )
        package = await generate_audit_package(db_session, configured_project.id)
        assert package["payroll_count"] == 3
        assert package["sub_count"] == 2  # ACME + Beta


# ===================================================================
# TestWageGuardAPI
# ===================================================================


class TestWageGuardAPI:
    """Tests for the HTTP API endpoints."""

    async def test_get_determinations(self, client, auth_headers, test_project):
        """GET determinations endpoint returns seeded data."""
        response = await client.get(
            f"/api/v1/projects/{test_project.id}/wages/determinations",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 3

    async def test_create_payroll_endpoint(self, client, auth_headers, test_project):
        """POST payrolls endpoint creates a payroll."""
        response = await client.post(
            f"/api/v1/projects/{test_project.id}/wages/payrolls",
            json={
                "contractor_name": "API Test Corp",
                "week_ending": "2025-01-11",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["contractor_name"] == "API Test Corp"
        assert data["payroll_number"] == 1

    async def test_list_payrolls_endpoint(self, client, auth_headers, test_project):
        """GET payrolls endpoint returns list."""
        response = await client.get(
            f"/api/v1/projects/{test_project.id}/wages/payrolls",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_apprenticeship_endpoint(self, client, auth_headers, test_project):
        """GET apprenticeship endpoint returns status."""
        response = await client.get(
            f"/api/v1/projects/{test_project.id}/wages/apprenticeship",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_labor_hours" in data
        assert "compliant" in data

    async def test_invite_sub_endpoint(self, client, auth_headers, test_project):
        """POST invite-sub endpoint returns success."""
        response = await client.post(
            f"/api/v1/projects/{test_project.id}/wages/invite-sub",
            json={
                "email": "sub@example.com",
                "contractor_name": "Sub Inc",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert "message" in data
