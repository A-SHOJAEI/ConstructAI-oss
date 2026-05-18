import logging
import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.services.security.rbac import RBACEnforcer
from app.services.security.redis_state import (
    blacklist_token as _redis_blacklist_token,
)
from app.services.security.redis_state import (
    is_token_blacklisted as _redis_is_token_blacklisted,
)
from app.utils.security import decode_access_token

logger = logging.getLogger(__name__)

# auto_error=False: allows cookie fallback when no Bearer header present
security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Token blacklist (Redis-backed with in-memory fallback)
# ---------------------------------------------------------------------------


async def blacklist_token(jti: str, ttl_seconds: int = 1800) -> None:
    """Add a token's JTI to the blacklist so it is rejected on future requests."""
    await _redis_blacklist_token(jti, ttl_seconds=ttl_seconds)


async def is_token_blacklisted(jti: str) -> bool:
    """Return True if the token has been blacklisted (e.g. via logout)."""
    return await _redis_is_token_blacklisted(jti)


# Paths that bypass email verification enforcement
_EMAIL_VERIFICATION_EXEMPT_PATHS = {
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/logout",
    "/api/v1/auth/mfa/setup",
    "/api/v1/auth/mfa/verify-setup",
}


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    # SECURITY [S3]: Helper to extract client IP for audit logging
    _client_ip = request.client.host if request.client else "unknown"

    # Try Bearer header first, then httpOnly cookie
    token = credentials.credentials if credentials else request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(token)
    if payload is None:
        # SECURITY [S3]: Audit failed token decode
        logger.warning(
            "SECURITY: Token verification failed — invalid token (decode failed) (IP: %s)",
            _client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # SECURITY [L-01]: Reject tokens without a JTI — they cannot be blacklisted
    # and would bypass logout/revocation checks entirely.
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing JTI claim"
        )
    if await is_token_blacklisted(jti):
        # SECURITY [S3]: Audit blacklisted token usage
        logger.warning(
            "SECURITY: Token verification failed — blacklisted token (jti=%s, IP: %s)",
            jti,
            _client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Reject tokens issued before a password reset (token_version mismatch)
    token_version = payload.get("token_version", 0)
    if token_version != (user.token_version or 0):
        # SECURITY [S3]: Audit token version mismatch (password changed)
        logger.warning(
            "SECURITY: Token verification failed — token_version mismatch (user=%s, IP: %s)",
            user_id,
            _client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been invalidated by a password change",
        )

    # RT6-AUTH-03: Validate org_id in token matches user's current org
    token_org_id = payload.get("org_id")
    if token_org_id and token_org_id != str(user.org_id):
        # SECURITY [S3]: Audit org mismatch (potential lateral movement)
        logger.warning(
            "SECURITY: Token verification failed — org mismatch "
            "(user=%s, token_org=%s, user_org=%s, IP: %s)",
            user_id,
            token_org_id,
            user.org_id,
            _client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token organization mismatch",
        )

    # Email verification enforcement
    if not user.email_verified:
        request_path = request.url.path
        if request_path not in _EMAIL_VERIFICATION_EXEMPT_PATHS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email verification required. Check your email for the verification link.",
            )

    return user


async def verify_project_access(
    project_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Project:
    """Verify the current user's org owns the project. Returns the project or raises 404."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.org_id == current_user.org_id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


# ---------------------------------------------------------------------------
# RBAC permission dependency
# ---------------------------------------------------------------------------
# NOTE: ABAC (Attribute-Based Access Control) is available at app/services/security/abac.py
# but is intentionally NOT enforced at the middleware/dependency level because it requires
# resource-level context (classification, phase, document type) that is only known after
# the resource is loaded. Services should call ABACPolicy.evaluate() explicitly when
# handling classified/phased resources. See abac.py for the policy evaluator.
# ---------------------------------------------------------------------------
_rbac = RBACEnforcer()


async def _get_project_member(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> ProjectMember | None:
    """Look up a user's project membership to get their project-scoped role."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def require_permission(resource: str, action: str):
    """FastAPI dependency that checks RBAC permission.

    Project-scoped: if ``project_id`` is in path params, uses
    ``ProjectMember.role`` for the check. Falls back to ``User.role``
    for org-level operations.

    Usage::

        @router.post("/projects")
        async def create_project(
            current_user: User = Depends(require_permission("projects", "create")),
        ):
            ...
    """

    async def _check(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        permission_action = f"{resource}:{action}"
        effective_role = current_user.role or "readonly"

        # Extract project_id from path params if present
        project_id = request.path_params.get("project_id")

        if project_id:
            try:
                project_uuid = uuid.UUID(str(project_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid project ID",
                ) from exc

            # SECURITY [M-02]: org_admin bypasses project membership check but
            # MUST still verify the project belongs to the user's organization.
            # Without this cross-org check, an org_admin from org A could access
            # projects belonging to org B via the wildcard "*" permission.
            if effective_role == "org_admin":
                project_check = await db.execute(
                    select(Project).where(
                        Project.id == project_uuid,
                        Project.org_id == current_user.org_id,
                    )
                )
                if project_check.scalar_one_or_none() is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Project not found",
                    )
            else:
                member = await _get_project_member(db, project_uuid, current_user.id)
                if member:
                    effective_role = member.role
                else:
                    # Not a member and not org_admin — deny access
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Project not found",
                    )

        if not _rbac.check_permission(effective_role, permission_action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check


def require_mfa():
    """FastAPI dependency that ensures the current user has MFA enabled.

    Use for sensitive operations like role changes, user management, etc.
    """

    async def _check(
        request: Request,
        current_user: User = Depends(get_current_user),
    ):
        if not current_user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA must be enabled for this operation",
            )
        return current_user

    return _check
