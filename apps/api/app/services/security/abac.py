"""Attribute-Based Access Control overlay for fine-grained policies."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

# Roles that are restricted to certain project phases.
# If a role is listed here it may only access resources whose
# ``project_phase`` attribute is in the allowed set.
_PHASE_RESTRICTIONS: dict[str, set[str]] = {
    "SUBCONTRACTOR": {
        "construction",
        "commissioning",
        "closeout",
    },
    "INSPECTOR": {
        "construction",
        "commissioning",
        "closeout",
    },
    "ARCHITECT_ENGINEER": {
        "design",
        "preconstruction",
        "construction",
    },
}

# Document types each role is allowed to access.
# An empty set means no restriction (all types allowed).
_DOC_TYPE_RESTRICTIONS: dict[str, set[str]] = {
    "SUBCONTRACTOR": {
        "drawing",
        "specification",
        "schedule",
        "daily_log",
        "safety_plan",
    },
    "READ_ONLY": {
        "report",
        "summary",
        "drawing",
    },
}


class ABACPolicy:
    """ABAC overlay for fine-grained access control.

    Evaluates policies based on:
    - User attributes (role, department, certifications)
    - Resource attributes (project phase, classification level)
    - Environment attributes (time, location, device type)
    """

    CLASSIFICATION_LEVELS: ClassVar[dict[str, int]] = {
        "public": 0,
        "internal": 1,
        "confidential": 2,
        "restricted": 3,
    }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
        action: str,
        environment: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Evaluate ABAC policy.

        Returns
        -------
        tuple[bool, str]
            ``(allowed, reason)`` where *reason* explains the
            decision.
        """
        environment = environment or {}

        # 1. Classification level check
        ok, reason = self._check_classification(
            user_attrs,
            resource_attrs,
        )
        if not ok:
            logger.info(
                "ABAC denied (classification): %s",
                reason,
            )
            return False, reason

        # 2. Project phase restrictions
        ok, reason = self._check_phase(
            user_attrs,
            resource_attrs,
        )
        if not ok:
            logger.info(
                "ABAC denied (phase): %s",
                reason,
            )
            return False, reason

        # 3. Document type restrictions
        ok, reason = self._check_document_type(
            user_attrs,
            resource_attrs,
        )
        if not ok:
            logger.info(
                "ABAC denied (doc_type): %s",
                reason,
            )
            return False, reason

        # 4. Location-based restrictions
        ok, reason = self._check_location(
            user_attrs,
            resource_attrs,
            environment,
        )
        if not ok:
            logger.info(
                "ABAC denied (location): %s",
                reason,
            )
            return False, reason

        logger.debug(
            "ABAC granted for action=%s user=%s",
            action,
            user_attrs.get("user_id", "unknown"),
        )
        return True, "Access granted"

    # ------------------------------------------------------------------ #
    # Private checks
    # ------------------------------------------------------------------ #

    def _check_classification(
        self,
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
    ) -> tuple[bool, str]:
        """User clearance must be >= resource classification."""
        resource_class = resource_attrs.get(
            "classification",
            "public",
        )
        user_clearance = user_attrs.get("clearance", "public")

        res_level = self.CLASSIFICATION_LEVELS.get(
            resource_class,
            0,
        )
        usr_level = self.CLASSIFICATION_LEVELS.get(
            user_clearance,
            0,
        )

        if usr_level < res_level:
            return (
                False,
                (
                    f"Insufficient clearance: user has "
                    f"'{user_clearance}' but resource requires "
                    f"'{resource_class}'"
                ),
            )
        return True, "Classification check passed"

    def _check_phase(
        self,
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
    ) -> tuple[bool, str]:
        """Some roles may only access certain project phases."""
        role = str(user_attrs.get("role", "")).upper()
        project_phase = resource_attrs.get("project_phase")

        if not project_phase:
            # No phase specified on resource -- allow
            return True, "No phase restriction"

        allowed_phases = _PHASE_RESTRICTIONS.get(role)
        if allowed_phases is None:
            # Role is not phase-restricted
            return True, "Role not phase-restricted"

        if project_phase.lower() not in allowed_phases:
            return (
                False,
                (f"Role '{role}' cannot access resources in phase '{project_phase}'"),
            )
        return True, "Phase check passed"

    def _check_document_type(
        self,
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
    ) -> tuple[bool, str]:
        """Restrict certain roles to specific document types."""
        role = str(user_attrs.get("role", "")).upper()
        doc_type = resource_attrs.get("document_type")

        if not doc_type:
            return True, "No document type restriction"

        allowed_types = _DOC_TYPE_RESTRICTIONS.get(role)
        if allowed_types is None:
            # Role has no document-type restriction
            return True, "Role not doc-type restricted"

        if doc_type.lower() not in allowed_types:
            return (
                False,
                (f"Role '{role}' cannot access document type '{doc_type}'"),
            )
        return True, "Document type check passed"

    @staticmethod
    def _check_location(
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
        environment: dict[str, Any],
    ) -> tuple[bool, str]:
        """Restricted resources require on-site or VPN access."""
        resource_class = resource_attrs.get(
            "classification",
            "public",
        )
        if resource_class != "restricted":
            return True, "Location check not required"

        location = environment.get("location", "")
        is_vpn = environment.get("vpn", False)

        if location == "on_site" or is_vpn:
            return True, "Location check passed"

        return (
            False,
            ("Restricted resources require on-site access or an active VPN connection"),
        )
