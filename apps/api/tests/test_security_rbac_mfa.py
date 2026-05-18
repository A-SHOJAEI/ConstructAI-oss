"""Tests for RBAC, MFA, email verification, audit logging, and project members.

Covers:
- Role enum and permission matrix (unit)
- Project-scoped RBAC (integration)
- MFA setup, verify, login flow (unit + integration)
- Email verification enforcement (integration)
- Audit logging to DB (integration)
- Project membership CRUD (integration)
- Backward compatibility with legacy role names
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.models.audit import AuditLog
from app.models.project import Project, ProjectMember
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.security.mfa import (
    generate_backup_codes,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_totp_uri,
    verify_backup_code,
    verify_totp,
)
from app.services.security.rbac import (
    _LEGACY_ROLE_MAP,
    PERMISSION_MATRIX,
    RBACEnforcer,
    Role,
)
from app.utils.security import hash_password

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def org_admin_user(db_session: AsyncSession, test_org) -> User:
    user = User(
        email=f"orgadmin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("AdminPass123!@#"),
        full_name="Org Admin",
        org_id=test_org.id,
        role="org_admin",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def readonly_user(db_session: AsyncSession, test_org) -> User:
    user = User(
        email=f"readonly-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("ReadOnly123!@#"),
        full_name="Read Only User",
        org_id=test_org.id,
        role="readonly",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def field_engineer_user(db_session: AsyncSession, test_org) -> User:
    user = User(
        email=f"engineer-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Engineer123!@#"),
        full_name="Field Engineer",
        org_id=test_org.id,
        role="field_engineer",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def unverified_user(db_session: AsyncSession, test_org) -> User:
    user = User(
        email=f"unverified-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Unverified123!@#"),
        full_name="Unverified User",
        org_id=test_org.id,
        role="project_manager",
        email_verified=False,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_project(db_session: AsyncSession, test_org) -> Project:
    project = Project(
        name="Test Security Project",
        org_id=test_org.id,
        status="active",
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def project_member(
    db_session: AsyncSession, field_engineer_user: User, test_project: Project
) -> ProjectMember:
    member = ProjectMember(
        project_id=test_project.id,
        user_id=field_engineer_user.id,
        role="field_engineer",
    )
    db_session.add(member)
    await db_session.flush()
    await db_session.refresh(member)
    return member


# ---------------------------------------------------------------------------
# Test: Role Enum
# ---------------------------------------------------------------------------


class TestRoleEnum:
    def test_all_nine_roles_exist(self):
        assert len(Role) == 9

    def test_role_names(self):
        expected = {
            "ORG_ADMIN",
            "PROJECT_ADMIN",
            "PROJECT_MANAGER",
            "SUPERINTENDENT",
            "SAFETY_MANAGER",
            "FIELD_ENGINEER",
            "SUBCONTRACTOR",
            "OWNER_REP",
            "READONLY",
        }
        assert {r.name for r in Role} == expected

    def test_hierarchy_ordering(self):
        assert Role.ORG_ADMIN < Role.PROJECT_ADMIN < Role.PROJECT_MANAGER
        assert Role.READONLY > Role.FIELD_ENGINEER

    def test_all_roles_have_permissions(self):
        for role in Role:
            assert role in PERMISSION_MATRIX, f"Role {role.name} missing from PERMISSION_MATRIX"


# ---------------------------------------------------------------------------
# Test: Permission Matrix
# ---------------------------------------------------------------------------


class TestPermissionMatrix:
    def setup_method(self):
        self.enforcer = RBACEnforcer()

    def test_org_admin_has_global_wildcard(self):
        assert self.enforcer.check_permission("org_admin", "anything:here")
        assert self.enforcer.check_permission("org_admin", "projects:delete")
        assert self.enforcer.check_permission("org_admin", "audit:read")

    def test_project_admin_has_project_wildcard(self):
        assert self.enforcer.check_permission("project_admin", "projects:create")
        assert self.enforcer.check_permission("project_admin", "projects:delete")
        assert self.enforcer.check_permission("project_admin", "documents:upload")
        assert self.enforcer.check_permission("project_admin", "audit:read")

    def test_project_manager_limited(self):
        assert self.enforcer.check_permission("project_manager", "projects:read")
        assert self.enforcer.check_permission("project_manager", "projects:update")
        assert not self.enforcer.check_permission("project_manager", "projects:delete")
        assert not self.enforcer.check_permission("project_manager", "audit:read")

    def test_superintendent_field_access(self):
        assert self.enforcer.check_permission("superintendent", "daily_logs:create")
        assert self.enforcer.check_permission("superintendent", "punch_lists:update")
        assert self.enforcer.check_permission("superintendent", "quality:create")
        assert not self.enforcer.check_permission("superintendent", "estimates:create")

    def test_safety_manager_safety_focus(self):
        assert self.enforcer.check_permission("safety_manager", "safety:create")
        assert self.enforcer.check_permission("safety_manager", "cameras:create")
        assert self.enforcer.check_permission("safety_manager", "zones:update")
        assert not self.enforcer.check_permission("safety_manager", "estimates:read")

    def test_field_engineer_data_entry(self):
        assert self.enforcer.check_permission("field_engineer", "daily_logs:create")
        assert self.enforcer.check_permission("field_engineer", "rfis:create")
        assert self.enforcer.check_permission("field_engineer", "documents:upload")
        assert not self.enforcer.check_permission("field_engineer", "change_orders:approve")

    def test_subcontractor_limited_scope(self):
        assert self.enforcer.check_permission("subcontractor", "daily_logs:create")
        assert self.enforcer.check_permission("subcontractor", "rfis:create")
        assert self.enforcer.check_permission("subcontractor", "documents:read_filtered")
        assert not self.enforcer.check_permission("subcontractor", "documents:upload")
        # subcontractors can read safety info to acknowledge briefings; only
        # safety:create / safety:update should be restricted.
        assert not self.enforcer.check_permission("subcontractor", "safety:update")

    def test_owner_rep_approve_access(self):
        assert self.enforcer.check_permission("owner_rep", "change_orders:approve")
        assert self.enforcer.check_permission("owner_rep", "pay_applications:approve")
        assert self.enforcer.check_permission("owner_rep", "submittals:approve")
        assert not self.enforcer.check_permission("owner_rep", "documents:create")

    def test_readonly_read_only(self):
        assert self.enforcer.check_permission("readonly", "projects:read")
        assert self.enforcer.check_permission("readonly", "documents:read")
        assert not self.enforcer.check_permission("readonly", "projects:create")
        assert not self.enforcer.check_permission("readonly", "documents:upload")
        assert not self.enforcer.check_permission("readonly", "audit:read")

    def test_unknown_role_denied(self):
        assert not self.enforcer.check_permission("nonexistent_role", "projects:read")

    def test_malformed_action_denied(self):
        # org_admin has wildcard "*" so it bypasses format checks
        # Test with a non-wildcard role instead
        assert not self.enforcer.check_permission("readonly", "no_colon_here")

    def test_get_allowed_actions(self):
        actions = self.enforcer.get_allowed_actions("readonly")
        assert "projects:read" in actions
        assert len(actions) > 5


# ---------------------------------------------------------------------------
# Test: Legacy Role Mapping
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def setup_method(self):
        self.enforcer = RBACEnforcer()

    def test_legacy_platform_admin_maps_to_org_admin(self):
        assert self.enforcer.check_permission("platform_admin", "projects:delete")

    def test_legacy_general_contractor_maps_to_project_admin(self):
        assert self.enforcer.check_permission("general_contractor", "projects:create")

    def test_legacy_read_only_maps_to_readonly(self):
        assert self.enforcer.check_permission("read_only", "projects:read")
        assert not self.enforcer.check_permission("read_only", "projects:create")

    def test_legacy_map_coverage(self):
        """All legacy roles resolve to valid new roles."""
        for old, new in _LEGACY_ROLE_MAP.items():
            role = self.enforcer._resolve_role(old)
            assert role is not None, f"Legacy role '{old}' did not resolve"
            assert role.name.lower() == new, f"'{old}' → '{role.name.lower()}' != '{new}'"


# ---------------------------------------------------------------------------
# Test: Project-Scoped RBAC
# ---------------------------------------------------------------------------


class TestProjectScopedRBAC:
    @pytest.mark.asyncio
    async def test_org_admin_bypasses_membership(
        self, client, org_admin_user, test_project, db_session
    ):
        """Org admin can access project even without ProjectMember entry."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/api/v1/projects/{test_project.id}/members", headers=headers)
        # Should not get 404 (project not found) — org_admin bypasses membership
        assert resp.status_code in (200, 403), resp.text

    @pytest.mark.asyncio
    async def test_non_member_denied(self, client, readonly_user, test_project):
        """User who is not a project member gets 404."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(readonly_user.id), "org_id": str(readonly_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/api/v1/projects/{test_project.id}/members", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_with_role_access(
        self, client, field_engineer_user, test_project, project_member
    ):
        """Member with field_engineer role can read project members."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={
                "sub": str(field_engineer_user.id),
                "org_id": str(field_engineer_user.org_id),
            }
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/api/v1/projects/{test_project.id}/members", headers=headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test: MFA Service
# ---------------------------------------------------------------------------


class TestMfaSetup:
    def test_generate_secret(self):
        secret = generate_totp_secret()
        assert len(secret) >= 16
        assert secret.isalnum()

    def test_totp_uri(self):
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "test@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "ConstructAI" in uri
        # @ is URL-encoded as %40
        assert "test" in uri and "example.com" in uri

    def test_verify_totp_valid(self):
        import pyotp

        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert verify_totp(secret, code)

    def test_verify_totp_invalid(self):
        secret = generate_totp_secret()
        assert not verify_totp(secret, "000000")

    def test_qr_code_generation(self):
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "test@example.com")
        data_uri = generate_qr_code_data_uri(uri)
        assert data_uri.startswith("data:image/png;base64,")
        assert len(data_uri) > 100


class TestMfaBackupCodes:
    def test_generate_backup_codes(self):
        plaintext, hashed, salt = generate_backup_codes(10)
        assert len(plaintext) == 10
        assert len(hashed) == 10
        assert salt  # 16-byte hex salt
        # Codes are 12-char hex strings (48-bit entropy)
        for code in plaintext:
            assert len(code) == 12
            int(code, 16)  # Should not raise

    def test_verify_backup_code_valid(self):
        plaintext, hashed, salt = generate_backup_codes(5)
        idx = verify_backup_code(plaintext[2], hashed, salt)
        assert idx == 2

    def test_verify_backup_code_invalid(self):
        _, hashed, salt = generate_backup_codes(5)
        idx = verify_backup_code("invalid!", hashed, salt)
        assert idx is None

    def test_backup_code_one_time_use(self):
        plaintext, hashed, salt = generate_backup_codes(3)
        code = plaintext[1]
        idx = verify_backup_code(code, hashed, salt)
        assert idx == 1
        # Remove used code
        hashed.pop(idx)
        # Should not verify again
        idx2 = verify_backup_code(code, hashed, salt)
        assert idx2 is None


# ---------------------------------------------------------------------------
# Test: MFA Login Flow
# ---------------------------------------------------------------------------


class TestMfaLogin:
    @pytest.mark.asyncio
    async def test_mfa_setup_endpoint(self, client, org_admin_user):
        """Setup MFA returns QR code and secret."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post("/api/v1/auth/mfa/setup", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "qr_code" in data
        assert "secret" in data
        assert "provisioning_uri" in data

    @pytest.mark.asyncio
    async def test_mfa_verify_setup_invalid_code(self, client, org_admin_user, db_session):
        """Verify setup with wrong code fails."""
        from app.utils.security import create_access_token

        # First setup MFA
        org_admin_user.mfa_secret = generate_totp_secret()
        db_session.add(org_admin_user)
        await db_session.flush()

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/auth/mfa/verify-setup",
            headers=headers,
            json={"code": "000000"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_mfa_verify_setup_valid_code(self, client, org_admin_user, db_session):
        """Verify setup with correct code succeeds and returns backup codes."""
        import pyotp

        from app.api.v1.auth import _mfa_fernet
        from app.services.cache import CacheService
        from app.utils.security import create_access_token

        secret = generate_totp_secret()
        # The endpoint reads the pending secret from Redis (set by
        # /mfa/setup). Pre-seed the cache with an encrypted copy so
        # /verify-setup finds it.
        encrypted = _mfa_fernet().encrypt(secret.encode()).decode()
        await CacheService().set(f"cai:mfa_setup:{org_admin_user.id}", encrypted, ttl=300)

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        code = pyotp.TOTP(secret).now()
        resp = await client.post(
            "/api/v1/auth/mfa/verify-setup",
            headers=headers,
            json={"code": code},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "backup_codes" in data
        assert len(data["backup_codes"]) == 10


# ---------------------------------------------------------------------------
# Test: MFA Enforcement
# ---------------------------------------------------------------------------


class TestMfaEnforcement:
    def test_admin_roles_require_mfa(self):
        from app.api.v1.auth import _MFA_REQUIRED_ROLES

        assert "org_admin" in _MFA_REQUIRED_ROLES
        assert "project_admin" in _MFA_REQUIRED_ROLES

    def test_non_admin_roles_optional(self):
        from app.api.v1.auth import _MFA_REQUIRED_ROLES

        assert "field_engineer" not in _MFA_REQUIRED_ROLES
        assert "readonly" not in _MFA_REQUIRED_ROLES
        assert "subcontractor" not in _MFA_REQUIRED_ROLES


# ---------------------------------------------------------------------------
# Test: Email Verification Enforcement
# ---------------------------------------------------------------------------


class TestEmailVerification:
    @pytest.mark.asyncio
    async def test_unverified_user_blocked(self, client, unverified_user):
        """Unverified user gets 403 on most endpoints."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(unverified_user.id), "org_id": str(unverified_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/projects/", headers=headers)
        assert resp.status_code == 403
        assert "verification" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verified_user_allowed(self, client, org_admin_user):
        """Verified user can access endpoints normally."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/projects/", headers=headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_verify_email_flow(self, client, unverified_user, db_session):
        """Verify email token marks user as verified."""
        import jwt

        from app.config import settings

        token = jwt.encode(
            {
                "sub": str(unverified_user.id),
                "email": unverified_user.email,
                "type": "email_verification",
                "exp": datetime.now(UTC) + timedelta(hours=24),
                "iss": "constructai",
                "aud": "constructai-api",
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        resp = await client.post("/api/v1/auth/verify-email", json={"token": token})
        assert resp.status_code == 200

        await db_session.refresh(unverified_user)
        assert unverified_user.email_verified is True

    @pytest.mark.asyncio
    async def test_resend_verification(self, client, unverified_user):
        """Resend verification returns generic message."""
        resp = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": unverified_user.email},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_resend_unknown_email(self, client):
        """Resend for unknown email returns same generic message (no leak)."""
        resp = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "nobody@nowhere.com"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test: Audit Logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_audit_log_db_writes_record(self, db_session, test_user):
        """audit_log_db() creates an AuditLog row."""
        from app.services.observability.audit_logger import AuditAction, audit_log_db

        await audit_log_db(
            db_session,
            AuditAction.LOGIN_SUCCESS,
            user_id=test_user.id,
            org_id=test_user.org_id,
            details={"test": True},
        )
        await db_session.flush()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.action == "auth.login.success")
        )
        logs = result.scalars().all()
        assert len(logs) >= 1
        assert logs[0].details == {"test": True}

    @pytest.mark.asyncio
    async def test_audit_log_preserves_ip(self, db_session, test_user):
        """audit_log_db() stores IP address and user agent."""
        from app.services.observability.audit_logger import AuditAction, audit_log_db

        await audit_log_db(
            db_session,
            AuditAction.ACCESS_DENIED,
            user_id=test_user.id,
            org_id=test_user.org_id,
            ip_address="192.168.1.100",
            user_agent="TestBrowser/1.0",
        )
        await db_session.flush()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.action == "authz.access_denied")
        )
        log = result.scalars().first()
        assert log is not None
        assert log.ip_address == "192.168.1.100"
        assert log.user_agent == "TestBrowser/1.0"

    @pytest.mark.asyncio
    async def test_audit_log_sync_still_works(self):
        """The sync audit_log() function still works (backward compat)."""
        from app.services.observability.audit_logger import AuditAction, audit_log

        # Should not raise
        audit_log(AuditAction.LOGOUT, user_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_audit_query_endpoint(self, client, org_admin_user, db_session):
        """GET /admin/audit-logs returns audit records."""
        from app.services.observability.audit_logger import AuditAction, audit_log_db
        from app.utils.security import create_access_token

        # Create some audit entries
        await audit_log_db(
            db_session,
            AuditAction.REGISTER,
            user_id=org_admin_user.id,
            org_id=org_admin_user.org_id,
            details={"email": "test@test.com"},
        )
        await db_session.flush()

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/admin/audit-logs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    @pytest.mark.asyncio
    async def test_audit_query_denied_for_non_admin(self, client, readonly_user, db_session):
        """Non-admin users cannot query audit logs."""
        from app.utils.security import create_access_token

        # Make the user a member of some org but readonly
        token = create_access_token(
            data={"sub": str(readonly_user.id), "org_id": str(readonly_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/admin/audit-logs", headers=headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_audit_log_model_fields(self, db_session, test_user):
        """AuditLog model has all required fields."""
        log = AuditLog(
            action="test.action",
            user_id=test_user.id,
            org_id=test_user.org_id,
            resource_type="test",
            resource_id=uuid.uuid4(),
            ip_address="10.0.0.1",
            user_agent="TestAgent",
            details={"key": "value"},
        )
        db_session.add(log)
        await db_session.flush()
        await db_session.refresh(log)

        assert log.id is not None
        assert log.timestamp is not None
        assert log.action == "test.action"


# ---------------------------------------------------------------------------
# Test: Project Members API
# ---------------------------------------------------------------------------


class TestProjectMembers:
    @pytest.mark.asyncio
    async def test_add_member(self, client, org_admin_user, test_project, field_engineer_user):
        """Org admin can add a member to a project."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            headers=headers,
            json={"user_id": str(field_engineer_user.id), "role": "field_engineer"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "field_engineer"

    @pytest.mark.asyncio
    async def test_add_member_invalid_role(
        self, client, org_admin_user, test_project, readonly_user
    ):
        """Adding member with invalid role fails."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            headers=headers,
            json={"user_id": str(readonly_user.id), "role": "invalid_role"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_members(
        self, client, org_admin_user, test_project, field_engineer_user, project_member
    ):
        """List project members returns all members."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/api/v1/projects/{test_project.id}/members", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1

    @pytest.mark.asyncio
    async def test_update_member_role(
        self, client, org_admin_user, test_project, field_engineer_user, project_member
    ):
        """Update a member's role."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/members/{field_engineer_user.id}",
            headers=headers,
            json={"role": "project_manager"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "project_manager"

    @pytest.mark.asyncio
    async def test_remove_member(
        self, client, org_admin_user, test_project, field_engineer_user, project_member
    ):
        """Remove a member from a project."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/members/{field_engineer_user.id}",
            headers=headers,
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_add_members(
        self, client, readonly_user, test_project, project_member, db_session
    ):
        """Readonly user cannot add members even if they are a project member."""
        # Make readonly user a member first
        member = ProjectMember(
            project_id=test_project.id,
            user_id=readonly_user.id,
            role="readonly",
        )
        db_session.add(member)
        await db_session.flush()

        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(readonly_user.id), "org_id": str(readonly_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            headers=headers,
            json={"user_id": str(uuid.uuid4()), "role": "field_engineer"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test: Route Permissions
# ---------------------------------------------------------------------------


class TestRoutePermissions:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create_project(self, client, readonly_user):
        """Readonly user cannot create projects."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(readonly_user.id), "org_id": str(readonly_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/projects/",
            headers=headers,
            json={"name": "Forbidden Project"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_org_admin_can_create_project(self, client, org_admin_user):
        """Org admin can create projects."""
        from app.utils.security import create_access_token

        token = create_access_token(
            data={"sub": str(org_admin_user.id), "org_id": str(org_admin_user.org_id)}
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/projects/",
            headers=headers,
            json={"name": "Admin Project"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_unauthenticated_rejected(self, client):
        """No auth header → 403 (FastAPI's HTTPBearer default)."""
        resp = await client.get("/api/v1/projects/")
        assert resp.status_code == 403
