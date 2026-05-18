import re
import uuid

from pydantic import BaseModel, Field, field_validator


def _validate_password_complexity(password: str) -> str:
    """Shared password-strength validation logic.

    Requires 12+ characters, at least one uppercase letter, one lowercase
    letter, one digit, and one special character.
    """
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise ValueError("Password must contain at least one special character")
    return password


def _validate_email_format(email: str) -> str:
    """Basic email format validation without external dependencies."""
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Invalid email format: must contain '@' and a '.' in the domain")
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        raise ValueError("Invalid email format: local part and domain must not be empty")
    if domain.startswith(".") or domain.endswith("."):
        raise ValueError("Invalid email format: domain must not start or end with '.'")
    return email.lower().strip()


class RegisterRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    org_id: uuid.UUID

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        return _validate_email_format(v)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_password_complexity(v)


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        return _validate_email_format(v)


class TokenResponse(BaseModel):
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None
    mfa_setup_required: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str = Field(default="", max_length=4096)


class MfaSetupResponse(BaseModel):
    qr_code: str
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MfaSetupVerifyResponse(BaseModel):
    backup_codes: list[str]
    message: str = "MFA enabled successfully. Save these backup codes securely."


class MfaLoginRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class ResendVerificationRequest(BaseModel):
    email: str = Field(max_length=255)
