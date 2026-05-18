"""Tests for the ABAC policy evaluator.

[security] ABACPolicy is a defense-in-depth gate on top of RBAC.
Pin every check (classification, project phase, document type,
location), the deny-on-first-failure short-circuit, and the
documented role-restriction tables.
"""

from __future__ import annotations

import pytest

from app.services.security.abac import (
    _DOC_TYPE_RESTRICTIONS,
    _PHASE_RESTRICTIONS,
    ABACPolicy,
)

# =========================================================================
# Module constants — pin canonical role-restriction tables
# =========================================================================


def test_phase_restrictions_canonical_roles():
    """Pin the documented phase-restriction roles. SUBCONTRACTOR and
    INSPECTOR shouldn't access pre-construction; ARCHITECT_ENGINEER
    shouldn't access closeout-phase resources."""
    assert "SUBCONTRACTOR" in _PHASE_RESTRICTIONS
    assert "INSPECTOR" in _PHASE_RESTRICTIONS
    assert "ARCHITECT_ENGINEER" in _PHASE_RESTRICTIONS
    # Subcontractor restricted to construction phases:
    assert "construction" in _PHASE_RESTRICTIONS["SUBCONTRACTOR"]
    assert "design" not in _PHASE_RESTRICTIONS["SUBCONTRACTOR"]


def test_doc_type_restrictions_canonical_roles():
    """SUBCONTRACTOR can't access RFIs or change orders directly —
    only drawings, specs, schedules, daily logs, and safety plans.
    READ_ONLY is even narrower."""
    assert "drawing" in _DOC_TYPE_RESTRICTIONS["SUBCONTRACTOR"]
    assert "specification" in _DOC_TYPE_RESTRICTIONS["SUBCONTRACTOR"]
    assert "rfi" not in _DOC_TYPE_RESTRICTIONS["SUBCONTRACTOR"]
    assert "change_order" not in _DOC_TYPE_RESTRICTIONS["SUBCONTRACTOR"]


def test_classification_levels_ordered():
    """Pin the classification ladder — public < internal < confidential
    < restricted. Tests rely on this ordering."""
    levels = ABACPolicy.CLASSIFICATION_LEVELS
    assert levels["public"] < levels["internal"]
    assert levels["internal"] < levels["confidential"]
    assert levels["confidential"] < levels["restricted"]


# =========================================================================
# Classification checks
# =========================================================================


@pytest.fixture
def policy() -> ABACPolicy:
    return ABACPolicy()


def test_public_clearance_can_read_public_resource(policy: ABACPolicy):
    user = {"role": "EMPLOYEE", "clearance": "public"}
    resource = {"classification": "public"}
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is True
    assert "granted" in reason.lower()


def test_internal_clearance_can_read_public_resource(policy: ABACPolicy):
    """Higher clearance can always access lower-classification resource."""
    user = {"role": "EMPLOYEE", "clearance": "internal"}
    resource = {"classification": "public"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_public_clearance_cannot_read_confidential(policy: ABACPolicy):
    """[security] Lower clearance MUST NOT access higher-classification
    resource."""
    user = {"role": "EMPLOYEE", "clearance": "public"}
    resource = {"classification": "confidential"}
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is False
    assert "clearance" in reason.lower()


def test_unknown_clearance_treated_as_public(policy: ABACPolicy):
    """An unknown clearance string defaults to level 0 (public). A
    user with garbage clearance can only access public resources."""
    user = {"role": "EMPLOYEE", "clearance": "elite-haxor"}
    resource = {"classification": "internal"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is False


def test_unknown_resource_class_treated_as_public(policy: ABACPolicy):
    """Unknown resource classification defaults to public — fail-open
    here is intentional, since the documented levels are exhaustive."""
    user = {"role": "EMPLOYEE", "clearance": "public"}
    resource = {"classification": "weird-class"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_no_classification_attribute_treated_as_public(policy: ABACPolicy):
    user = {"role": "EMPLOYEE"}  # no clearance attr
    resource = {}  # no classification attr
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


# =========================================================================
# Phase restrictions
# =========================================================================


def test_subcontractor_blocked_from_design_phase(policy: ABACPolicy):
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "design"}
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is False
    assert "phase" in reason.lower()


def test_subcontractor_allowed_in_construction_phase(policy: ABACPolicy):
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "construction"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_architect_engineer_blocked_from_closeout(policy: ABACPolicy):
    user = {"role": "ARCHITECT_ENGINEER", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "closeout"}
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is False
    assert "phase" in reason.lower()


def test_architect_engineer_allowed_in_design(policy: ABACPolicy):
    user = {"role": "ARCHITECT_ENGINEER", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "design"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_unrestricted_role_passes_phase_check(policy: ABACPolicy):
    """A role NOT in _PHASE_RESTRICTIONS is unrestricted on phase."""
    user = {"role": "PROJECT_MANAGER", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "design"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_phase_check_case_insensitive(policy: ABACPolicy):
    """The phase comparison must be case-insensitive — "Construction"
    should match "construction"."""
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "Construction"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_role_check_case_insensitive(policy: ABACPolicy):
    """The role comparison must be case-insensitive — lowercase
    "subcontractor" should still trigger phase restrictions."""
    user = {"role": "subcontractor", "clearance": "internal"}
    resource = {"classification": "internal", "project_phase": "design"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is False


def test_no_project_phase_skips_phase_check(policy: ABACPolicy):
    """If the resource has no phase metadata, the phase check is
    bypassed."""
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal"}  # no project_phase
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


# =========================================================================
# Document-type restrictions
# =========================================================================


def test_subcontractor_blocked_from_change_order(policy: ABACPolicy):
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal", "document_type": "change_order"}
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is False
    assert "document" in reason.lower()


def test_subcontractor_allowed_drawing(policy: ABACPolicy):
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal", "document_type": "drawing"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_read_only_allowed_report(policy: ABACPolicy):
    user = {"role": "READ_ONLY", "clearance": "public"}
    resource = {"classification": "public", "document_type": "report"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_read_only_blocked_from_specification(policy: ABACPolicy):
    """READ_ONLY can see reports / summaries / drawings — but not specs."""
    user = {"role": "READ_ONLY", "clearance": "public"}
    resource = {"classification": "public", "document_type": "specification"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is False


def test_unrestricted_role_passes_doc_type_check(policy: ABACPolicy):
    user = {"role": "PROJECT_MANAGER", "clearance": "internal"}
    resource = {"classification": "internal", "document_type": "change_order"}
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_no_doc_type_skips_doc_type_check(policy: ABACPolicy):
    user = {"role": "SUBCONTRACTOR", "clearance": "internal"}
    resource = {"classification": "internal"}  # no document_type
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


# =========================================================================
# Location restrictions — restricted resources only
# =========================================================================


def test_restricted_resource_off_site_no_vpn_blocked(policy: ABACPolicy):
    """[security] Restricted resources require on-site OR VPN —
    accessing from a public network must be blocked."""
    user = {"role": "ADMIN", "clearance": "restricted"}
    resource = {"classification": "restricted"}
    env = {"location": "remote_cafe", "vpn": False}
    allowed, reason = policy.evaluate(user, resource, "read", env)
    assert allowed is False
    assert "vpn" in reason.lower() or "on-site" in reason.lower()


def test_restricted_resource_on_site_allowed(policy: ABACPolicy):
    user = {"role": "ADMIN", "clearance": "restricted"}
    resource = {"classification": "restricted"}
    env = {"location": "on_site", "vpn": False}
    allowed, _ = policy.evaluate(user, resource, "read", env)
    assert allowed is True


def test_restricted_resource_vpn_allowed(policy: ABACPolicy):
    user = {"role": "ADMIN", "clearance": "restricted"}
    resource = {"classification": "restricted"}
    env = {"location": "remote", "vpn": True}
    allowed, _ = policy.evaluate(user, resource, "read", env)
    assert allowed is True


def test_non_restricted_resource_skips_location_check(policy: ABACPolicy):
    """Confidential and below don't require VPN — only "restricted"."""
    user = {"role": "ADMIN", "clearance": "confidential"}
    resource = {"classification": "confidential"}
    # No env at all → must still pass:
    allowed, _ = policy.evaluate(user, resource, "read")
    assert allowed is True


def test_environment_default_to_empty_dict(policy: ABACPolicy):
    """If caller passes None environment, it should be treated as empty."""
    user = {"role": "EMPLOYEE", "clearance": "public"}
    resource = {"classification": "public"}
    allowed, _ = policy.evaluate(user, resource, "read", environment=None)
    assert allowed is True


# =========================================================================
# Layered checks — first failure wins
# =========================================================================


def test_classification_failure_short_circuits_other_checks(policy: ABACPolicy):
    """If clearance fails, we don't even reach the phase / doc-type
    checks. The reason should mention "clearance", not phase."""
    user = {"role": "SUBCONTRACTOR", "clearance": "public"}
    resource = {
        "classification": "confidential",
        "project_phase": "design",  # would also fail
        "document_type": "change_order",  # would also fail
    }
    allowed, reason = policy.evaluate(user, resource, "read")
    assert allowed is False
    assert "clearance" in reason.lower()
